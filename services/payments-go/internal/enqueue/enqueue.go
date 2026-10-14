// Package enqueue hands an accepted event to the Python API's internal endpoint.
package enqueue

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"time"

	cloudtasks "cloud.google.com/go/cloudtasks/apiv2"
	taskspb "cloud.google.com/go/cloudtasks/apiv2/cloudtaskspb"
	"github.com/googleapis/gax-go/v2"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/dedupe"
)

const InternalPath = "/internal/payments/events"

// Enqueuer delivers the original raw payload to the API. Implementations must be safe for concurrent use.
type Enqueuer interface {
	// TaskName is the deterministic task identifier recorded with the delivery ("" when not applicable).
	TaskName(k dedupe.Key) string
	Enqueue(ctx context.Context, k dedupe.Key, body []byte) error
}

// TaskCreator is the slice of the Cloud Tasks client used here (fakeable in tests).
type TaskCreator interface {
	CreateTask(ctx context.Context, req *taskspb.CreateTaskRequest, opts ...gax.CallOption) (*taskspb.Task, error)
}

type CloudTasks struct {
	Client    TaskCreator
	Queue     string
	TargetURL string
	SAEmail   string
	Audience  string
}

// NewCloudTasks builds the real client from Application Default Credentials.
func NewCloudTasks(ctx context.Context, queue, targetURL, saEmail, audience string) (*CloudTasks, func() error, error) {
	c, err := cloudtasks.NewClient(ctx)
	if err != nil {
		return nil, nil, err
	}
	return &CloudTasks{Client: c, Queue: queue, TargetURL: targetURL, SAEmail: saEmail, Audience: audience}, c.Close, nil
}

func (c *CloudTasks) TaskName(k dedupe.Key) string { return c.Queue + "/tasks/" + k.Hash() }

// Enqueue creates the task. ALREADY_EXISTS means an earlier attempt (e.g. one whose DB commit failed) already
// queued this event, which is success. Cloud Tasks keeps a task name reserved for a while after the task ran,
// so a late sender retry is absorbed here too; the API's own idempotency covers anything beyond that window.
func (c *CloudTasks) Enqueue(ctx context.Context, k dedupe.Key, body []byte) error {
	_, err := c.Client.CreateTask(ctx, &taskspb.CreateTaskRequest{
		Parent: c.Queue,
		Task: &taskspb.Task{
			Name: c.TaskName(k),
			MessageType: &taskspb.Task_HttpRequest{HttpRequest: &taskspb.HttpRequest{
				Url:        c.TargetURL + InternalPath,
				HttpMethod: taskspb.HttpMethod_POST,
				Headers:    map[string]string{"Content-Type": "application/json"},
				Body:       body,
				AuthorizationHeader: &taskspb.HttpRequest_OidcToken{OidcToken: &taskspb.OidcToken{
					ServiceAccountEmail: c.SAEmail,
					Audience:            c.Audience,
				}},
			}},
		},
	})
	if status.Code(err) == codes.AlreadyExists {
		return nil
	}
	return err
}

// Direct posts synchronously with X-Internal-Token. Local/dev only: no retry, no queue.
type Direct struct {
	HTTP      *http.Client
	TargetURL string
	Token     string
}

func NewDirect(targetURL, token string) *Direct {
	return &Direct{HTTP: &http.Client{Timeout: 5 * time.Second}, TargetURL: targetURL, Token: token}
}

func (d *Direct) TaskName(dedupe.Key) string { return "" }

func (d *Direct) Enqueue(ctx context.Context, _ dedupe.Key, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.TargetURL+InternalPath, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", d.Token)
	resp, err := d.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("internal endpoint returned status %d", resp.StatusCode)
	}
	return nil
}
