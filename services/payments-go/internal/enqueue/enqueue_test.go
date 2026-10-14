package enqueue

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	taskspb "cloud.google.com/go/cloudtasks/apiv2/cloudtaskspb"
	"github.com/googleapis/gax-go/v2"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/aindrajaya/imageopz-server/services/payments-go/internal/dedupe"
)

var key = dedupe.Key{Provider: "lemonsqueezy", EventName: "order_created", ObjectType: "orders", ObjectID: "42"}

type fakeClient struct {
	req *taskspb.CreateTaskRequest
	err error
}

func (f *fakeClient) CreateTask(_ context.Context, r *taskspb.CreateTaskRequest, _ ...gax.CallOption) (*taskspb.Task, error) {
	f.req = r
	return &taskspb.Task{}, f.err
}

func newCT(f *fakeClient) *CloudTasks {
	return &CloudTasks{Client: f, Queue: "projects/p/locations/l/queues/q", TargetURL: "https://api.example",
		SAEmail: "sa@p.iam.gserviceaccount.com", Audience: "https://api.example"}
}

func TestCloudTasksRequestShape(t *testing.T) {
	f := &fakeClient{}
	body := []byte(`{"raw":  "payload"}`)
	if err := newCT(f).Enqueue(context.Background(), key, body); err != nil {
		t.Fatal(err)
	}
	h := f.req.Task.GetHttpRequest()
	if f.req.Parent != "projects/p/locations/l/queues/q" ||
		f.req.Task.Name != "projects/p/locations/l/queues/q/tasks/"+key.Hash() ||
		h.Url != "https://api.example/internal/payments/events" || h.HttpMethod != taskspb.HttpMethod_POST ||
		string(h.Body) != string(body) || h.GetOidcToken().ServiceAccountEmail != "sa@p.iam.gserviceaccount.com" ||
		h.GetOidcToken().Audience != "https://api.example" {
		t.Fatalf("unexpected request: %+v", f.req)
	}
}

func TestCloudTasksErrors(t *testing.T) {
	tests := []struct {
		name    string
		err     error
		wantErr bool
	}{
		{"already exists is success", status.Error(codes.AlreadyExists, "dup"), false},
		{"unavailable is failure", status.Error(codes.Unavailable, "down"), true},
		{"plain error is failure", errors.New("boom"), true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := newCT(&fakeClient{err: tc.err}).Enqueue(context.Background(), key, []byte("{}"))
			if (err != nil) != tc.wantErr {
				t.Fatalf("got %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}

func TestDirect(t *testing.T) {
	var gotTok, gotPath string
	var gotBody []byte
	code := 200
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotTok, gotPath = r.Header.Get("X-Internal-Token"), r.URL.Path
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(code)
	}))
	defer srv.Close()
	d := NewDirect(srv.URL, "tok")
	if err := d.Enqueue(context.Background(), key, []byte(`{"x":1}`)); err != nil {
		t.Fatal(err)
	}
	if gotTok != "tok" || gotPath != InternalPath || string(gotBody) != `{"x":1}` {
		t.Fatalf("bad request: %q %q %q", gotTok, gotPath, gotBody)
	}
	for _, c := range []int{401, 500, 302} {
		code = c
		if err := d.Enqueue(context.Background(), key, nil); err == nil {
			t.Fatalf("status %d must be an error", c)
		}
	}
	if d.TaskName(key) != "" {
		t.Fatal("direct mode has no task name")
	}
}
