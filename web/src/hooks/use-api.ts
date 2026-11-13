"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, sessionRequest, unwrap, unwrapPage } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { leaveTo } from "@/lib/navigation";

type Schemas = components["schemas"];

/** Query keys, grouped so a mutation can invalidate everything it affects. */
export const keys = {
  me: ["me"] as const,
  instruments: (search: string) => ["instruments", search] as const,
  candles: (id: string, range: string) => ["candles", id, range] as const,
  strategyTypes: ["strategy-types"] as const,
  strategies: ["strategies"] as const,
  strategy: (id: string) => ["strategies", id] as const,
  strategyVersions: (id: string) => ["strategies", id, "versions"] as const,
  experiments: ["experiments"] as const,
  experiment: (id: string) => ["experiments", id] as const,
  portfolios: ["portfolios"] as const,
  portfolio: (id: string) => ["portfolios", id] as const,
  risk: ["risk"] as const,
};

// ------------------------------------------------------------------- account
export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(api.GET("/api/v1/auth/me")),
    staleTime: 60_000,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => sessionRequest("logout"),
    onSettled: () => {
      queryClient.clear();
      leaveTo("/login");
    },
  });
}

// --------------------------------------------------------------- market data
export function useInstruments(search = "") {
  return useQuery({
    queryKey: keys.instruments(search),
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/market-data/instruments", {
          params: { query: { search: search || undefined, limit: 200 } },
        }),
      ),
    staleTime: 5 * 60_000,
  });
}

export function useCandles(instrumentId: string | undefined, start: string, interval: string) {
  return useQuery({
    queryKey: keys.candles(instrumentId ?? "", `${start}:${interval}`),
    enabled: Boolean(instrumentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/market-data/{instrument_id}/historical", {
          params: {
            path: { instrument_id: instrumentId! },
            query: { start, interval, limit: 5000 },
          },
        }),
      ),
    staleTime: 5 * 60_000,
  });
}

// ---------------------------------------------------------------- strategies
export function useStrategyTypes() {
  return useQuery({
    queryKey: keys.strategyTypes,
    queryFn: () => unwrap(api.GET("/api/v1/strategies/types")),
    staleTime: Infinity,
  });
}

export function useStrategies(includeArchived = false) {
  return useQuery({
    queryKey: [...keys.strategies, { includeArchived }],
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/strategies", {
          params: { query: { include_archived: includeArchived, limit: 200 } },
        }),
      ),
  });
}

export function useStrategy(id: string) {
  return useQuery({
    queryKey: keys.strategy(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/strategies/{strategy_id}", { params: { path: { strategy_id: id } } })),
  });
}

export function useStrategyVersions(id: string) {
  return useQuery({
    queryKey: keys.strategyVersions(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/strategies/{strategy_id}/versions", {
          params: { path: { strategy_id: id } },
        }),
      ),
  });
}

export function useCreateStrategy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["StrategyCreate"]) => unwrap(api.POST("/api/v1/strategies", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.strategies }),
  });
}

export function useUpdateStrategy(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["StrategyUpdate"]) =>
      unwrap(
        api.PATCH("/api/v1/strategies/{strategy_id}", {
          params: { path: { strategy_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.strategies }),
  });
}

export function useCreateStrategyVersion(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["StrategyVersionCreate"]) =>
      unwrap(
        api.POST("/api/v1/strategies/{strategy_id}/versions", {
          params: { path: { strategy_id: id } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.strategies }),
  });
}

// --------------------------------------------------------------- experiments
export function useExperiments() {
  return useQuery({
    queryKey: keys.experiments,
    queryFn: () => unwrapPage(api.GET("/api/v1/experiments", { params: { query: { limit: 200 } } })),
    // Keep the list fresh while anything is still queued or running.
    refetchInterval: (query) =>
      query.state.data?.items.some((e) => e.status === "QUEUED" || e.status === "RUNNING")
        ? 2000
        : false,
  });
}

export function useExperiment(id: string | undefined) {
  return useQuery({
    queryKey: keys.experiment(id ?? ""),
    enabled: Boolean(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/experiments/{experiment_id}", {
          params: { path: { experiment_id: id! } },
        }),
      ),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "QUEUED" || status === "RUNNING" ? 1500 : false;
    },
  });
}

export function useRunBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["BacktestRequest"]) =>
      unwrap(
        api.POST("/api/v1/experiments/backtests", { params: { query: { async: true } }, body }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.experiments }),
  });
}

export function useDeleteExperiment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/experiments/{experiment_id}", {
          params: { path: { experiment_id: id } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.experiments }),
  });
}

// ---------------------------------------------------------------- portfolios
export function usePortfolios() {
  return useQuery({
    queryKey: keys.portfolios,
    queryFn: () => unwrapPage(api.GET("/api/v1/portfolios", { params: { query: { limit: 200 } } })),
  });
}

export function usePortfolio(id: string) {
  return useQuery({
    queryKey: keys.portfolio(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/portfolios/{portfolio_id}", { params: { path: { portfolio_id: id } } })),
  });
}

export function useValuation(id: string | undefined) {
  return useQuery({
    queryKey: [...keys.portfolio(id ?? ""), "valuation"],
    enabled: Boolean(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/portfolios/{portfolio_id}/valuation", {
          params: { path: { portfolio_id: id! } },
        }),
      ),
    refetchInterval: 60_000,
  });
}

export function useOrders(id: string) {
  return useQuery({
    queryKey: [...keys.portfolio(id), "orders"],
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/portfolios/{portfolio_id}/orders", {
          params: { path: { portfolio_id: id }, query: { limit: 200 } },
        }),
      ),
    refetchInterval: (query) =>
      query.state.data?.items.some((order) => order.status === "OPEN") ? 15_000 : false,
  });
}

export function useOrderDetail(portfolioId: string, orderId: string | null) {
  return useQuery({
    queryKey: [...keys.portfolio(portfolioId), "orders", orderId],
    enabled: Boolean(orderId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/portfolios/{portfolio_id}/orders/{order_id}", {
          params: { path: { portfolio_id: portfolioId, order_id: orderId! } },
        }),
      ),
  });
}

export function useTrades(id: string) {
  return useQuery({
    queryKey: [...keys.portfolio(id), "trades"],
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/portfolios/{portfolio_id}/trades", {
          params: { path: { portfolio_id: id }, query: { limit: 200 } },
        }),
      ),
  });
}

export function useLedger(id: string) {
  return useQuery({
    queryKey: [...keys.portfolio(id), "ledger"],
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/portfolios/{portfolio_id}/ledger", {
          params: { path: { portfolio_id: id }, query: { limit: 200 } },
        }),
      ),
  });
}

export function useLedgerBalances(id: string) {
  return useQuery({
    queryKey: [...keys.portfolio(id), "ledger-balances"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/portfolios/{portfolio_id}/ledger/balances", {
          params: { path: { portfolio_id: id } },
        }),
      ),
  });
}

export function useSnapshots(id: string | undefined) {
  return useQuery({
    queryKey: [...keys.portfolio(id ?? ""), "snapshots"],
    enabled: Boolean(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/portfolios/{portfolio_id}/snapshots", {
          params: { path: { portfolio_id: id! } },
        }),
      ),
  });
}

export function useCreatePortfolio() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["PortfolioCreate"]) => unwrap(api.POST("/api/v1/portfolios", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.portfolios }),
  });
}

/** Mutations on one portfolio all refresh everything known about it. */
function usePortfolioMutation<TBody, TResult>(mutationFn: (body: TBody) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.portfolios }),
  });
}

export function useMoveCash(id: string) {
  return usePortfolioMutation((body: Schemas["CashMovementCreate"]) =>
    unwrap(
      api.POST("/api/v1/portfolios/{portfolio_id}/cash", {
        params: { path: { portfolio_id: id } },
        body,
      }),
    ),
  );
}

export function useSubmitOrder(id: string) {
  return usePortfolioMutation(
    ({ order, idempotencyKey }: { order: Schemas["OrderCreate"]; idempotencyKey: string }) =>
      unwrap(
        api.POST("/api/v1/portfolios/{portfolio_id}/orders", {
          params: { path: { portfolio_id: id }, header: { "idempotency-key": idempotencyKey } },
          body: order,
        }),
      ),
  );
}

export function useCancelOrder(id: string) {
  return usePortfolioMutation((orderId: string) =>
    unwrap(
      api.POST("/api/v1/portfolios/{portfolio_id}/orders/{order_id}/cancel", {
        params: { path: { portfolio_id: id, order_id: orderId } },
      }),
    ),
  );
}

export function useRecordSnapshot(id: string) {
  return usePortfolioMutation(() =>
    unwrap(
      api.POST("/api/v1/portfolios/{portfolio_id}/snapshots", {
        params: { path: { portfolio_id: id } },
      }),
    ),
  );
}

// ---------------------------------------------------------------------- risk
export function useRiskReports(filter: { portfolioId?: string; experimentId?: string } = {}) {
  return useQuery({
    queryKey: [...keys.risk, filter],
    queryFn: () =>
      unwrapPage(
        api.GET("/api/v1/risk/reports", {
          params: {
            query: {
              portfolio_id: filter.portfolioId,
              experiment_id: filter.experimentId,
              limit: 200,
            },
          },
        }),
      ),
  });
}

export function useCreateRiskReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RiskReportCreate"]) =>
      unwrap(api.POST("/api/v1/risk/reports", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.risk }),
  });
}
