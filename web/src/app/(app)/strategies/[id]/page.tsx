"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import {
  ParameterForm,
  ParameterList,
  type ParameterSchema,
  type ParameterValues,
  defaultValues,
  toParameters,
} from "@/components/parameter-form";
import { ErrorState, LoadingRows, Spinner } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
  useCreateStrategyVersion,
  useStrategy,
  useStrategyTypes,
  useStrategyVersions,
  useUpdateStrategy,
} from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import type { Strategy } from "@/lib/api/types";
import { formatDateTime, strategyLabel } from "@/lib/format";

function RenameForm({ strategy }: { strategy: Strategy }) {
  const update = useUpdateStrategy(strategy.id);
  const [name, setName] = useState(strategy.name);
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        update.mutate({ name }, { onSuccess: () => toast.success("Renamed") });
      }}
    >
      <Field label="Name" className="flex-1">
        {(props) => (
          <Input
            {...props}
            required
            maxLength={150}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        )}
      </Field>
      <Button
        type="submit"
        variant="outline"
        disabled={update.isPending || !name.trim() || name === strategy.name}
      >
        Rename
      </Button>
    </form>
  );
}

function NewVersionForm({ strategy }: { strategy: Strategy }) {
  const types = useStrategyTypes();
  const create = useCreateStrategyVersion(strategy.id);
  const [values, setValues] = useState<ParameterValues | null>(null);

  const type = types.data?.find((candidate) => candidate.type === strategy.strategy_type);
  const schema = (type?.parameter_schema ?? {}) as ParameterSchema;
  const current =
    values ??
    defaultValues(strategy.latest_version?.parameters ?? type?.default_parameters ?? {});

  if (!Object.keys(schema.properties ?? {}).length) {
    return <p className="text-sm text-muted-foreground">This strategy has no parameters to change.</p>;
  }
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate(
          { parameters: toParameters(schema, current) },
          {
            onSuccess: (version) => {
              toast.success(`Added version ${version.version}`);
              setValues(null);
            },
          },
        );
      }}
    >
      <ParameterForm schema={schema} values={current} onChange={setValues} />
      <FormError message={create.error ? errorMessage(create.error) : null} />
      <Button type="submit" disabled={create.isPending}>
        {create.isPending ? <Spinner /> : null}
        Save as new version
      </Button>
    </form>
  );
}

export default function StrategyPage() {
  const { id } = useParams<{ id: string }>();
  const strategy = useStrategy(id);
  const versions = useStrategyVersions(id);
  const update = useUpdateStrategy(id);

  if (strategy.isPending) return <LoadingRows />;
  if (strategy.isError) {
    return <ErrorState error={strategy.error} onRetry={() => strategy.refetch()} />;
  }
  const data = strategy.data;

  return (
    <>
      <PageHeader
        back={{ href: "/strategies", label: "Strategies" }}
        title={
          <>
            {data.name}
            {data.is_archived ? (
              <Badge variant="secondary" className="ml-3 align-middle">
                archived
              </Badge>
            ) : null}
          </>
        }
        description={`${strategyLabel(data.strategy_type)}${data.description ? ` · ${data.description}` : ""}`}
        actions={
          <>
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() =>
                update.mutate(
                  { is_archived: !data.is_archived },
                  { onSuccess: () => toast.success(data.is_archived ? "Restored" : "Archived") },
                )
              }
            >
              {data.is_archived ? "Restore" : "Archive"}
            </Button>
            {data.latest_version ? (
              <Link
                href={`/backtests/new?strategy=${data.id}&version=${data.latest_version.id}`}
                className={buttonVariants()}
              >
                Backtest this
              </Link>
            ) : null}
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Change parameters</CardTitle>
            <CardDescription>
              Versions are never edited. Saving adds a version and leaves earlier backtests
              pointing at the parameters they ran with.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <NewVersionForm key={data.latest_version?.id} strategy={data} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardContent>
            <RenameForm key={data.name} strategy={data} />
          </CardContent>
        </Card>
      </div>

      <h2 className="mt-8 mb-3 text-lg font-semibold">Versions</h2>
      {versions.isPending ? (
        <LoadingRows rows={2} />
      ) : versions.isError ? (
        <ErrorState error={versions.error} onRetry={() => versions.refetch()} />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Parameters</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...versions.data].sort((a, b) => b.version - a.version).map((version) => (
                <TableRow key={version.id}>
                  <TableCell className="font-medium">v{version.version}</TableCell>
                  <TableCell>
                    <ParameterList parameters={version.parameters} />
                  </TableCell>
                  <TableCell>{formatDateTime(version.created_at)}</TableCell>
                  <TableCell className="text-right">
                    <Link
                      href={`/backtests/new?strategy=${data.id}&version=${version.id}`}
                      className={buttonVariants({ variant: "ghost", size: "sm" })}
                    >
                      Backtest
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  );
}
