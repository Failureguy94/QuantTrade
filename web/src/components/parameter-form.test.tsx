import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
  ParameterForm,
  ParameterList,
  type ParameterSchema,
  type ParameterValues,
  defaultValues,
  toParameters,
} from "./parameter-form";

const SCHEMA: ParameterSchema = {
  properties: {
    lookback: { type: "integer", description: "Rolling window in bars", minimum: 2, maximum: 2000 },
    entry_z: { type: "number", description: "Z-score to enter at", exclusiveMinimum: 0, maximum: 10 },
  },
};

function Harness() {
  const [values, setValues] = useState<ParameterValues>(defaultValues({ lookback: 20, entry_z: 2 }));
  return (
    <>
      <ParameterForm schema={SCHEMA} values={values} onChange={setValues} />
      <output>{JSON.stringify(toParameters(SCHEMA, values))}</output>
    </>
  );
}

describe("ParameterForm", () => {
  it("builds a labelled number input per schema property, with its range", () => {
    render(<Harness />);
    const lookback = screen.getByLabelText("Lookback");
    expect(lookback).toHaveValue(20);
    expect(lookback).toHaveAttribute("step", "1");
    expect(lookback).toHaveAccessibleDescription("Rolling window in bars · 2 to 2000");
    const entry = screen.getByLabelText("Entry z");
    expect(entry).toHaveAttribute("step", "any");
    expect(entry).toHaveAccessibleDescription("Z-score to enter at · above 0 to 10");
  });

  it("reports edits as numbers", async () => {
    render(<Harness />);
    const lookback = screen.getByLabelText("Lookback");
    await userEvent.clear(lookback);
    await userEvent.type(lookback, "35");
    expect(screen.getByRole("status")).toHaveTextContent('{"lookback":35,"entry_z":2}');
  });

  it("says so when a strategy has no parameters", () => {
    render(<ParameterForm schema={{ properties: {} }} values={{}} onChange={() => {}} />);
    expect(screen.getByText("This strategy has no parameters.")).toBeInTheDocument();
  });
});

describe("toParameters", () => {
  it("leaves out blank inputs so the API applies its default", () => {
    expect(toParameters(SCHEMA, { lookback: "", entry_z: "1.5" })).toEqual({ entry_z: 1.5 });
  });

  it("ignores values the schema does not know", () => {
    expect(toParameters(SCHEMA, { lookback: "5", stray: "9" })).toEqual({ lookback: 5 });
  });
});

describe("ParameterList", () => {
  it("lists parameters, or says there are none", () => {
    const { rerender } = render(<ParameterList parameters={{ fast_window: 10, slow_window: 30 }} />);
    expect(screen.getByText(/fast_window=10\s+slow_window=30/)).toBeInTheDocument();
    rerender(<ParameterList parameters={{}} />);
    expect(screen.getByText("None")).toBeInTheDocument();
  });
});
