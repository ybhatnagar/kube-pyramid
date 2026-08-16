// Package connectors defines the ingestion extension point. A Connector is one
// (source × data-type); users add a source by implementing an interface and
// self-registering into the registry (plugin pattern), so `--source prometheus`
// resolves to the right implementation with no core changes. Every connector
// emits normalized store records, so the store/engine never learn source formats.
package connectors

import (
	"context"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/kube-pyramid/collector/internal/store"
)

// Window is the [Start, End] range to pull at Step resolution.
type Window struct {
	Start time.Time
	End   time.Time
	Step  time.Duration
}

// AuthConfig carries out-of-cluster auth for a connector (all optional; in-cluster
// defaults need none). TLS material is added in a later milestone.
type AuthConfig struct {
	Bearer   string
	Username string
	Password string
}

// Config is per-run connector configuration. Endpoint + auth are always
// config-driven (never hardcoded) so the same binary works in- and out-of-cluster.
type Config struct {
	ClusterID  int64
	Endpoint   string
	Namespaces []string // empty = all namespaces
	Resources  []string // e.g. ["cpu","memory"]
	Auth       AuthConfig
	Extra      map[string]string // connector-specific overrides (e.g. PromQL templates)
}

// MetricsResult is what a MetricsConnector returns: normalized samples plus the
// workload identities it observed (so the metrics Step can populate disc_workloads,
// which the engine joins on workload_uid for card labels + cost).
type MetricsResult struct {
	Samples   []store.MetricSample
	Workloads []store.WorkloadIdentity
}

// MetricsConnector pulls normalized metric samples from a source.
type MetricsConnector interface {
	Name() string
	HealthCheck(ctx context.Context, cfg Config) error
	FetchMetrics(ctx context.Context, w Window, cfg Config) (MetricsResult, error)
}

// AllocationsConnector pulls the N-dimensional allocation vector (requests/limits per
// resource, incl. extended/custom resources) for the QoS clusterer. Allocations are
// current-state, so the connector typically reads the latest value in the Window.
type AllocationsConnector interface {
	Name() string
	HealthCheck(ctx context.Context, cfg Config) error
	FetchAllocations(ctx context.Context, w Window, cfg Config) ([]store.AllocationRow, error)
}

// InteractionConnector pulls dependency-graph edges from an interaction source
// (hubble | istio | otel), registered by name behind this one interface.
type InteractionConnector interface {
	Name() string
	HealthCheck(ctx context.Context, cfg Config) error
	FetchInteractions(ctx context.Context, w Window, cfg Config) ([]store.Interaction, error)
}

// --- registry --------------------------------------------------------------

var (
	regMu      sync.RWMutex
	metricsReg = map[string]MetricsConnector{}
	allocReg   = map[string]AllocationsConnector{}
	interReg   = map[string]InteractionConnector{}
)

// RegisterMetrics adds a metrics connector, keyed by Name(). Called from a
// connector package's init(); duplicate names panic (a programming error).
func RegisterMetrics(c MetricsConnector) {
	regMu.Lock()
	defer regMu.Unlock()
	if _, dup := metricsReg[c.Name()]; dup {
		panic("connectors: duplicate metrics connector " + c.Name())
	}
	metricsReg[c.Name()] = c
}

// RegisterAllocations adds an allocations connector, keyed by Name().
func RegisterAllocations(c AllocationsConnector) {
	regMu.Lock()
	defer regMu.Unlock()
	if _, dup := allocReg[c.Name()]; dup {
		panic("connectors: duplicate allocations connector " + c.Name())
	}
	allocReg[c.Name()] = c
}

// RegisterInteraction adds an interaction connector, keyed by Name().
func RegisterInteraction(c InteractionConnector) {
	regMu.Lock()
	defer regMu.Unlock()
	if _, dup := interReg[c.Name()]; dup {
		panic("connectors: duplicate interaction connector " + c.Name())
	}
	interReg[c.Name()] = c
}

// Metrics resolves a registered metrics connector by name.
func Metrics(name string) (MetricsConnector, error) {
	regMu.RLock()
	defer regMu.RUnlock()
	c, ok := metricsReg[name]
	if !ok {
		return nil, fmt.Errorf("no metrics connector registered for %q", name)
	}
	return c, nil
}

// Allocations resolves a registered allocations connector by name.
func Allocations(name string) (AllocationsConnector, error) {
	regMu.RLock()
	defer regMu.RUnlock()
	c, ok := allocReg[name]
	if !ok {
		return nil, fmt.Errorf("no allocations connector registered for %q", name)
	}
	return c, nil
}

// Interaction resolves a registered interaction connector by name.
func Interaction(name string) (InteractionConnector, error) {
	regMu.RLock()
	defer regMu.RUnlock()
	c, ok := interReg[name]
	if !ok {
		return nil, fmt.Errorf("no interaction connector registered for %q", name)
	}
	return c, nil
}

// Names lists registered connectors for `collector connectors list`.
func Names() (metrics, allocations, interactions []string) {
	regMu.RLock()
	defer regMu.RUnlock()
	for n := range metricsReg {
		metrics = append(metrics, n)
	}
	for n := range allocReg {
		allocations = append(allocations, n)
	}
	for n := range interReg {
		interactions = append(interactions, n)
	}
	sort.Strings(metrics)
	sort.Strings(allocations)
	sort.Strings(interactions)
	return metrics, allocations, interactions
}
