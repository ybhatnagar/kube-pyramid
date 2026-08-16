package steps

import (
	"context"

	"github.com/kube-pyramid/collector/internal/connectors"
)

// AllocationsStep pulls the N-dimensional allocation vector via the selected
// AllocationsConnector (kube-state-metrics requests/limits, incl. extended/custom
// resources) and upserts it to the allocations table. It also records the workload
// identities it observed so the engine can join uids -> namespace/kind/name.
type AllocationsStep struct{}

func (AllocationsStep) Name() string { return "allocations" }

func (AllocationsStep) Run(ctx context.Context, rt Runtime) (Result, error) {
	ac, err := connectors.Allocations(rt.Source)
	if err != nil {
		return Result{}, err
	}
	rows, err := ac.FetchAllocations(ctx, rt.Window, rt.Cfg)
	if err != nil {
		return Result{}, err
	}
	n, err := rt.Store.UpsertAllocations(ctx, rows)
	if err != nil {
		return Result{}, err
	}
	return Result{RowsWritten: n}, nil
}
