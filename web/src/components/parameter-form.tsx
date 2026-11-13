"use client";

import { Field } from "@/components/field";
import { Input } from "@/components/ui/input";
import { humanize } from "@/lib/format";

type PropertySchema = {
  type?: string;
  title?: string;
  description?: string;
  default?: number;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
};

export type ParameterSchema = { properties?: Record<string, PropertySchema> };
export type ParameterValues = Record<string, string>;

export function defaultValues(defaults: Record<string, unknown>): ParameterValues {
  return Object.fromEntries(Object.entries(defaults).map(([key, value]) => [key, String(value)]));
}

/** Convert the text in the inputs to the numbers the API expects. */
export function toParameters(schema: ParameterSchema, values: ParameterValues) {
  return Object.fromEntries(
    Object.keys(schema.properties ?? {})
      .filter((key) => values[key] !== undefined && values[key] !== "")
      .map((key) => [key, Number(values[key])]),
  );
}

function rangeHint(property: PropertySchema): string {
  const low = property.minimum ?? property.exclusiveMinimum;
  const high = property.maximum ?? property.exclusiveMaximum;
  const range =
    low !== undefined && high !== undefined
      ? `${property.exclusiveMinimum !== undefined ? "above " : ""}${low} to ${high}`
      : "";
  return [property.description, range].filter(Boolean).join(" · ");
}

/** Inputs for a strategy type's parameters, generated from its JSON schema. */
export function ParameterForm({
  schema,
  values,
  onChange,
}: {
  schema: ParameterSchema;
  values: ParameterValues;
  onChange: (values: ParameterValues) => void;
}) {
  const properties = Object.entries(schema.properties ?? {});
  if (properties.length === 0) {
    return <p className="text-sm text-muted-foreground">This strategy has no parameters.</p>;
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {properties.map(([key, property]) => (
        <Field key={key} label={humanize(key)} hint={rangeHint(property)}>
          {(props) => (
            <Input
              {...props}
              type="number"
              inputMode="decimal"
              step={property.type === "integer" ? 1 : "any"}
              min={property.minimum}
              max={property.maximum}
              value={values[key] ?? ""}
              onChange={(event) => onChange({ ...values, [key]: event.target.value })}
            />
          )}
        </Field>
      ))}
    </div>
  );
}

export function ParameterList({ parameters }: { parameters: Record<string, unknown> }) {
  const entries = Object.entries(parameters);
  if (entries.length === 0) return <span className="text-muted-foreground">None</span>;
  return (
    <span className="font-mono text-xs">
      {entries.map(([key, value]) => `${key}=${String(value)}`).join("  ")}
    </span>
  );
}
