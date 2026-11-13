"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { PortfolioLedger, PortfolioTrades } from "@/components/portfolio/ledger";
import { PortfolioOrders } from "@/components/portfolio/orders";
import { CashDialog, PortfolioOverview } from "@/components/portfolio/overview";
import { ErrorState, LoadingRows } from "@/components/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePortfolio } from "@/hooks/use-api";
import { formatMoney } from "@/lib/format";

export default function PortfolioPage() {
  const { id } = useParams<{ id: string }>();
  const portfolio = usePortfolio(id);
  const [movingCash, setMovingCash] = useState(false);

  if (portfolio.isPending) return <LoadingRows rows={6} />;
  if (portfolio.isError) {
    return <ErrorState error={portfolio.error} onRetry={() => portfolio.refetch()} />;
  }
  const data = portfolio.data;

  return (
    <>
      <PageHeader
        back={{ href: "/portfolios", label: "Portfolios" }}
        title={data.name}
        description={`Paper account · ${data.base_currency} · started with ${formatMoney(data.initial_capital, data.base_currency)}`}
        actions={
          <>
            <Button variant="outline" onClick={() => setMovingCash(true)}>
              Deposit or withdraw
            </Button>
            <Link href={`/risk?portfolio=${data.id}`} className={buttonVariants({ variant: "outline" })}>
              Measure risk
            </Link>
          </>
        }
      />
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="ledger">Ledger</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="pt-4">
          <PortfolioOverview portfolio={data} />
        </TabsContent>
        <TabsContent value="orders" className="pt-4">
          <PortfolioOrders portfolio={data} />
        </TabsContent>
        <TabsContent value="trades" className="pt-4">
          <PortfolioTrades portfolio={data} />
        </TabsContent>
        <TabsContent value="ledger" className="pt-4">
          <PortfolioLedger portfolio={data} />
        </TabsContent>
      </Tabs>
      {movingCash ? <CashDialog portfolio={data} onClose={() => setMovingCash(false)} /> : null}
    </>
  );
}
