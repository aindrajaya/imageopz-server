// Package config reads the service configuration from the environment and fails closed.
package config

import (
	"errors"
	"fmt"
	"strings"
)

const (
	ModeCloudTasks = "cloudtasks"
	ModeDirect     = "direct"
)

type Config struct {
	Port                 string
	DatabaseURL          string
	SigningSecret        string
	EnqueueMode          string
	TasksQueue           string
	TasksTargetURL       string
	TasksSAEmail         string
	TasksAudience        string
	InternalSharedSecret string
}

// Load validates the environment. Any missing or unknown required value is an error: the process must not start.
func Load(getenv func(string) string) (Config, error) {
	c := Config{
		Port:                 getenv("PORT"),
		DatabaseURL:          getenv("DATABASE_URL"),
		SigningSecret:        getenv("LEMONSQUEEZY_SIGNING_SECRET"),
		EnqueueMode:          getenv("ENQUEUE_MODE"),
		TasksQueue:           getenv("TASKS_QUEUE"),
		TasksTargetURL:       strings.TrimRight(getenv("TASKS_TARGET_URL"), "/"),
		TasksSAEmail:         getenv("TASKS_SA_EMAIL"),
		TasksAudience:        getenv("TASKS_AUDIENCE"),
		InternalSharedSecret: getenv("INTERNAL_SHARED_SECRET"),
	}
	if c.Port == "" {
		c.Port = "8080"
	}
	var missing []string
	need := func(name, v string) {
		if v == "" {
			missing = append(missing, name)
		}
	}
	need("DATABASE_URL", c.DatabaseURL)
	need("LEMONSQUEEZY_SIGNING_SECRET", c.SigningSecret)
	need("TASKS_TARGET_URL", c.TasksTargetURL)
	switch c.EnqueueMode {
	case ModeCloudTasks:
		if c.TasksTargetURL != "" && !strings.HasPrefix(c.TasksTargetURL, "https://") {
			return Config{}, errors.New("TASKS_TARGET_URL must be https in cloudtasks mode")
		}
		need("TASKS_QUEUE", c.TasksQueue)
		need("TASKS_SA_EMAIL", c.TasksSAEmail)
		need("TASKS_AUDIENCE", c.TasksAudience)
		if c.TasksQueue != "" && !strings.HasPrefix(c.TasksQueue, "projects/") {
			return Config{}, errors.New("TASKS_QUEUE must look like projects/P/locations/L/queues/Q")
		}
	case ModeDirect:
		need("INTERNAL_SHARED_SECRET", c.InternalSharedSecret)
	case "":
		return Config{}, errors.New("ENQUEUE_MODE is required (cloudtasks or direct)")
	default:
		return Config{}, fmt.Errorf("unknown ENQUEUE_MODE %q (cloudtasks or direct)", c.EnqueueMode)
	}
	if len(missing) > 0 {
		return Config{}, fmt.Errorf("missing required environment: %s", strings.Join(missing, ", "))
	}
	return c, nil
}
