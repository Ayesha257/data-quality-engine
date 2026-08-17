import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EntityResolutionPanel, { flattenResolutions } from "../components/EntityResolutionPanel.jsx";

describe("flattenResolutions", () => {
  it("expands column resolutions into table rows", () => {
    const rows = flattenResolutions({
      columns: {
        City: {
          entity_type: "city",
          resolutions: {
            LHR: { candidate: "Lahore", confidence: 0.99, decision: "auto_match", tier: "lookup" },
          },
        },
      },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].original).toBe("LHR");
    expect(rows[0].candidate).toBe("Lahore");
    expect(rows[0].decision).toBe("auto_match");
  });
});

describe("EntityResolutionPanel", () => {
  const sampleSheets = [
    {
      sheet_name: "Sheet1",
      enabled: true,
      summary: { auto_match: 1, review: 1, no_match: 0 },
      entity_resolution_auto: 1,
      entity_resolution_review: 1,
      entity_resolution_no_match: 0,
      columns: {
        City: {
          entity_type: "city",
          resolutions: {
            LHR: {
              candidate: "Lahore",
              confidence: 0.99,
              decision: "auto_match",
              tier: "lookup",
            },
            KHI: {
              candidate: "Karachi",
              confidence: 0.76,
              decision: "review",
              tier: "fuzzy",
            },
          },
        },
      },
    },
  ];

  it("renders summary counts and hides details by default until toggled", () => {
    render(<EntityResolutionPanel sheets={sampleSheets} />);

    expect(screen.getByText("Standardized values")).toBeInTheDocument();
    expect(screen.getByText("Sheet1")).toBeInTheDocument();
    expect(screen.getByText("Matched values — Sheet1")).toBeInTheDocument();
    expect(screen.getByText("View details")).toBeInTheDocument();

    // Table rows are hidden by default
    expect(screen.queryByText("LHR")).toBeNull();
    expect(screen.queryByText("Lahore")).toBeNull();

    // Click to expand details
    fireEvent.click(screen.getByRole("button", { name: /matched values — sheet1/i }));

    expect(screen.getByText("Hide details")).toBeInTheDocument();
    expect(screen.getByText("LHR")).toBeInTheDocument();
    expect(screen.getByText("Lahore")).toBeInTheDocument();
    expect(screen.getByText("99%")).toBeInTheDocument();
    expect(screen.getAllByText("Auto").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Review").length).toBeGreaterThan(0);

    // Click again to collapse
    fireEvent.click(screen.getByRole("button", { name: /matched values — sheet1/i }));
    expect(screen.getByText("View details")).toBeInTheDocument();
    expect(screen.queryByText("LHR")).toBeNull();
  });

  it("supports defaultOpen prop when provided", () => {
    render(<EntityResolutionPanel sheets={sampleSheets} defaultOpen={true} />);
    expect(screen.getByText("Hide details")).toBeInTheDocument();
    expect(screen.getByText("LHR")).toBeInTheDocument();
  });

  it("renders nothing when no sheets have ER data", () => {
    const { container } = render(<EntityResolutionPanel sheets={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

