// Command ingress is the LemonSqueezy webhook ingress: verify, dedupe, enqueue.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/config"
	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/enqueue"
	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/httpapi"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("fatal", "error", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load(os.Getenv)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	var enq enqueue.Enqueuer
	if cfg.EnqueueMode == config.ModeCloudTasks {
		ct, closeFn, err := enqueue.NewCloudTasks(ctx, cfg.TasksQueue, cfg.TasksTargetURL, cfg.TasksSAEmail, cfg.TasksAudience)
		if err != nil {
			return err
		}
		defer func() { _ = closeFn() }()
		enq = ct
	} else {
		log.Warn("ENQUEUE_MODE=direct: local/dev only, no queue and no retries")
		enq = enqueue.NewDirect(cfg.TasksTargetURL, cfg.InternalSharedSecret)
	}

	store, err := httpapi.OpenPG(ctx, cfg.DatabaseURL)
	if err != nil {
		return err
	}
	defer store.Close()

	h := &httpapi.Handler{Secret: cfg.SigningSecret, Store: store, Enqueuer: enq, Log: log}
	srv := &http.Server{
		Addr:              ":" + cfg.Port,
		Handler:           h.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()
	log.Info("listening", "port", cfg.Port, "enqueue_mode", cfg.EnqueueMode)

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
	}
	log.Info("shutting down")
	shutCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutCtx); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
