"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Field, FormError, NativeSelect } from "@/components/field";
import { PageHeader } from "@/components/page-header";
import {
  ParameterForm,
  ParameterList,
  type ParameterSchema,
  type ParameterValues,
  defaultValues,
  toParameters,
} from "@/components/parameter-form";
import { EmptyState, ErrorState, LoadingRows, Spinner } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { Textarea } from "@/components/ui/textarea";
import { useCreateStrategy, useStrategies, useStrategyTypes } from "@/hooks/use-api";
import { errorMessage } from "@/lib/api/client";
import { formatDate, strategyLabel } from "@/lib/format";

function CreateStrategyDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const types = useStrategyTypes();
  const create = useCreateStrategy();
  const [typeName, setTypeName] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<ParameterValues | null>(null);

  const selected = types.data?.find((type) => type.type === typeName) ?? types.data?.[0];
  const schema = (selected?.parameter_schema ?? {}) as ParameterSchema;
  const current = values ?? defaultValues(selected?.default_parameters ?? {});

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    create.mutate(
      {
        name,
        description: description || null,
        strategy_type: selected.type,
        parameters: toParameters(schema, current),
      },
      {
        onSuccess: (strategy) => {
          toast.success(`Created ${strategy.name}`);
          onOpenChange(false);
          router.push(`/strategies/${strategy.id}`);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>New strategy</DialogTitle>
          <DialogDescription>
            Pick a type and its parameters. Changing parameters later adds a new version.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Type" hint={selected?.description}>
            {(props) => (
              <NativeSelect
                {...props}
                value={selected?.type ?? ""}
                onChange={(event) => {
                  setTypeName(event.target.value);
                  setValues(null);
                }}
              >
                {types.data?.map((type) => (
                  <option key={type.type} value={type.type}>
                    {type.name}
                  </option>
                ))}
              </NativeSelect>
            )}
          </Field>
          <Field label="Name">
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
          <Field label="Description (optional)">
            {(props) => (
              <Textarea
                {...props}
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            )}
          </Field>
          <ParameterForm schema={schema} values={current} onChange={setValues} />
          <FormError message={create.error ? errorMessage(create.error) : null} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !name.trim() || !selected}>
              {create.isPending ? <Spinner /> : null}
              Create strategy
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function StrategiesPage() {
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const strategies = useStrategies(showArchived);

  return (
    <>
      <PageHeader
        title="Strategies"
        description="Trading rules with versioned parameters. Backtests always point at one exact version."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden />
            New strategy
          </Button>
        }
      />
      <label className="mb-4 flex w-fit items-center gap-2 text-sm text-muted-foreground">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
        Show archived
      </label>

      {strategies.isPending ? (
        <LoadingRows />
      ) : strategies.isError ? (
        <ErrorState error={strategies.error} onRetry={() => strategies.refetch()} />
      ) : strategies.data.items.length === 0 ? (
        <EmptyState
          title="No strategies yet"
          description="Create one to start backtesting. Buy and hold is a good benchmark to begin with."
          action={<Button onClick={() => setCreating(true)}>New strategy</Button>}
        />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Latest version</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {strategies.data.items.map((strategy) => (
                <TableRow key={strategy.id}>
                  <TableCell className="font-medium">
                    <Link href={`/strategies/${strategy.id}`} className="hover:underline">
                      {strategy.name}
                    </Link>
                    {strategy.is_archived ? (
                      <Badge variant="secondary" className="ml-2">
                        archived
                      </Badge>
                    ) : null}
                  </TableCell>
                  <TableCell>{strategyLabel(strategy.strategy_type)}</TableCell>
                  <TableCell>
                    {strategy.latest_version ? (
                      <>
                        <span className="mr-2 text-muted-foreground">
                          v{strategy.latest_version.version}
                        </span>
                        <ParameterList parameters={strategy.latest_version.parameters} />
                      </>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>{formatDate(strategy.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {creating ? <CreateStrategyDialog open onOpenChange={setCreating} /> : null}
    </>
  );
}
