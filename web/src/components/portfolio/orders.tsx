"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Field, FormError, NativeSelect } from "@/components/field";
import { EmptyState, ErrorState, LoadingRows, Spinner } from "@/components/states";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCancelOrder,
  useInstruments,
  useOrderDetail,
  useOrders,
  useSubmitOrder,
} from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import type { Order, Portfolio } from "@/lib/api/types";
import { formatDateTime, formatMoney, formatNumber, humanize } from "@/lib/format";

function newKey(): string {
  return crypto.randomUUID();
}

function OrderTicket({ portfolio }: { portfolio: Portfolio }) {
  const instruments = useInstruments();
  const submit = useSubmitOrder(portfolio.id);
  const [instrumentId, setInstrumentId] = useState("");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [quantity, setQuantity] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  // One key per order the user intends. Retrying the same submission (a double
  // click, a network retry) reuses it, so it can never place the order twice.
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);
  const [outcome, setOutcome] = useState<Order | null>(null);

  const tradable = (instruments.data?.items ?? []).filter(
    (instrument) => instrument.currency_code === portfolio.base_currency,
  );
  const instrument = tradable.find((candidate) => candidate.id === instrumentId) ?? tradable[0];

  function place(event: React.FormEvent) {
    event.preventDefault();
    if (!instrument) return;
    setOutcome(null);
    submit.mutate(
      {
        idempotencyKey,
        order: {
          instrument_id: instrument.id,
          side,
          order_type: type,
          quantity,
          limit_price: type === "LIMIT" ? limitPrice : null,
        },
      },
      {
        onSuccess: (order) => {
          setOutcome(order);
          setIdempotencyKey(newKey());
          if (order.status === "REJECTED") {
            toast.error(order.rejection_reason ?? "The order was rejected");
          } else {
            toast.success(`Order ${humanize(order.status).toLowerCase()}`);
            setQuantity("");
          }
        },
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Place an order</CardTitle>
        <CardDescription>
          Market orders fill at the latest daily price plus slippage. Limit orders wait until the
          price reaches your limit.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {instruments.isPending ? (
          <LoadingRows rows={3} />
        ) : tradable.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No instrument is priced in {portfolio.base_currency}, so this portfolio has nothing it
            can trade.
          </p>
        ) : (
          <form onSubmit={place} className="space-y-4">
            <Field label="Instrument">
              {(props) => (
                <NativeSelect
                  {...props}
                  value={instrument?.id ?? ""}
                  onChange={(event) => setInstrumentId(event.target.value)}
                >
                  {tradable.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.symbol} · {candidate.name}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Side">
                {(props) => (
                  <NativeSelect
                    {...props}
                    value={side}
                    onChange={(event) => setSide(event.target.value as typeof side)}
                  >
                    <option value="BUY">Buy</option>
                    <option value="SELL">Sell</option>
                  </NativeSelect>
                )}
              </Field>
              <Field label="Type">
                {(props) => (
                  <NativeSelect
                    {...props}
                    value={type}
                    onChange={(event) => setType(event.target.value as typeof type)}
                  >
                    <option value="MARKET">Market</option>
                    <option value="LIMIT">Limit</option>
                  </NativeSelect>
                )}
              </Field>
              <Field label="Quantity">
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min="0"
                    step="any"
                    required
                    value={quantity}
                    onChange={(event) => setQuantity(event.target.value)}
                  />
                )}
              </Field>
              {type === "LIMIT" ? (
                <Field label={`Limit price (${portfolio.base_currency})`}>
                  {(props) => (
                    <Input
                      {...props}
                      type="number"
                      min="0"
                      step="any"
                      required
                      value={limitPrice}
                      onChange={(event) => setLimitPrice(event.target.value)}
                    />
                  )}
                </Field>
              ) : null}
            </div>
            <FormError message={submit.error ? errorMessage(submit.error) : null} />
            {outcome?.status === "REJECTED" ? (
              <FormError message={`Rejected: ${outcome.rejection_reason ?? "no reason given"}`} />
            ) : null}
            <Button
              type="submit"
              disabled={submit.isPending || !quantity || (type === "LIMIT" && !limitPrice)}
            >
              {submit.isPending ? <Spinner /> : null}
              {side === "BUY" ? "Buy" : "Sell"} {instrument?.symbol}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function OrderDialog({
  portfolio,
  orderId,
  symbol,
  onClose,
}: {
  portfolio: Portfolio;
  orderId: string;
  symbol: string;
  onClose: () => void;
}) {
  const detail = useOrderDetail(portfolio.id, orderId);
  const currency = portfolio.base_currency;
  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Order · {symbol}</DialogTitle>
          <DialogDescription>What happened to this order, in sequence.</DialogDescription>
        </DialogHeader>
        {detail.isPending ? (
          <LoadingRows rows={3} />
        ) : detail.isError ? (
          <ErrorState error={detail.error} />
        ) : (
          <div className="space-y-4 text-sm">
            <ol className="space-y-3 border-l pl-4">
              {detail.data.events.map((event) => (
                <li key={event.sequence}>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={event.to_status} />
                    <span className="text-xs text-muted-foreground">
                      {formatDateTime(event.created_at)}
                    </span>
                  </div>
                  {event.reason ? <p className="mt-1 text-muted-foreground">{event.reason}</p> : null}
                </li>
              ))}
            </ol>
            {detail.data.trades.length ? (
              <div>
                <h3 className="mb-1 font-medium">Fills</h3>
                <ul className="space-y-1">
                  {detail.data.trades.map((trade) => (
                    <li key={trade.id} className="flex justify-between gap-4 tabular-nums">
                      <span>
                        {formatNumber(trade.quantity, 4)} at {formatMoney(trade.fill_price, currency)}
                      </span>
                      <span className="text-muted-foreground">
                        commission {formatMoney(trade.commission, currency)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function PortfolioOrders({ portfolio }: { portfolio: Portfolio }) {
  const orders = useOrders(portfolio.id);
  const instruments = useInstruments();
  const cancel = useCancelOrder(portfolio.id);
  const [openOrder, setOpenOrder] = useState<Order | null>(null);
  const currency = portfolio.base_currency;

  const symbols = new Map(
    (instruments.data?.items ?? []).map((instrument) => [instrument.id, instrument.symbol]),
  );
  const symbolOf = (order: Order) => symbols.get(order.instrument_id) ?? "…";

  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <OrderTicket portfolio={portfolio} />
      <Card>
        <CardHeader>
          <CardTitle>Orders</CardTitle>
          <CardDescription>
            {orders.data ? `${orders.data.total} in total. ` : ""}Select an order to see its history.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {orders.isPending ? (
            <LoadingRows />
          ) : orders.isError ? (
            <ErrorState error={orders.error} onRetry={() => orders.refetch()} />
          ) : orders.data.items.length === 0 ? (
            <EmptyState title="No orders yet" />
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Placed</TableHead>
                    <TableHead>Order</TableHead>
                    <TableHead className="text-right">Quantity</TableHead>
                    <TableHead className="text-right">Limit</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {orders.data.items.map((order) => (
                    <TableRow key={order.id}>
                      <TableCell className="whitespace-nowrap">
                        {formatDateTime(order.created_at)}
                      </TableCell>
                      <TableCell>
                        <button
                          type="button"
                          className="font-medium hover:underline"
                          aria-label={`View order: ${humanize(order.side)} ${symbolOf(order)}`}
                          onClick={() => setOpenOrder(order)}
                        >
                          {humanize(order.side)} {symbolOf(order)}
                        </button>
                        <span className="block text-xs text-muted-foreground">
                          {humanize(order.order_type)}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(order.quantity, 4)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {order.limit_price ? formatMoney(order.limit_price, currency) : "—"}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={order.status} />
                        {order.rejection_reason ? (
                          <span className="mt-1 block max-w-56 text-xs text-muted-foreground">
                            {order.rejection_reason}
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right">
                        {order.status === "OPEN" ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={cancel.isPending}
                            onClick={() =>
                              cancel.mutate(order.id, {
                                onSuccess: () => toast.success("Order cancelled"),
                                onError: (error) => toast.error(errorMessage(error)),
                              })
                            }
                          >
                            Cancel
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
      {openOrder ? (
        <OrderDialog
          portfolio={portfolio}
          orderId={openOrder.id}
          symbol={symbolOf(openOrder)}
          onClose={() => setOpenOrder(null)}
        />
      ) : null}
    </div>
  );
}
