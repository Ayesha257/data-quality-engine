import { useState } from "react";

/**
 * Form-based business-rule builder. The client never writes YAML/JSON --
 * they fill in one row per rule (column pattern, operator, value,
 * severity), and this component assembles the equivalent YAML that the
 * existing dry-run/save endpoints already accept (api.dryRunRules /
 * api.saveClientRules both take a raw `rules_yaml` string -- see
 * src/api/client.js -- so no backend change is required).
 */

const OPERATORS = [
  { value: "< 0", label: "is negative" },
  { value: ">= 0", label: "is zero or positive" },
  { value: "> 0", label: "is positive" },
  { value: "== 0", label: "equals zero" },
  { value: "duplicate rate > 0", label: "has duplicates" },
  { value: "is null", label: "is empty / missing" },
  { value: "is not null", label: "is required (not empty)" },
];

const SEVERITIES = ["low", "medium", "high"];

const emptyRule = () => ({
  id: crypto.randomUUID(),
  ruleId: "",
  columnPattern: "",
  operator: OPERATORS[0].value,
  severity: "medium",
  description: "",
});

// Turns the form rows into the same YAML shape the backend already parses
// (thresholds: {...} / business_rules: [{rule_id, description, condition,
// severity}, ...]) -- matches the "Active ruleset" shape shown above it.
function buildYaml(thresholds, rules) {
  const thresholdLines = Object.entries(thresholds)
    .filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `  ${k}: ${v}`)
    .join("\n");

  const ruleLines = rules
    .filter((r) => r.ruleId.trim() && r.columnPattern.trim())
    .map((r) => {
      const condition = `value ${r.operator} for columns matching '${r.columnPattern.trim()}'`;
      const desc = r.description.trim() || `Auto-generated rule for ${r.columnPattern.trim()}`;
      return [
        `  - rule_id: "${r.ruleId.trim()}"`,
        `    description: "${desc}"`,
        `    condition: "${condition}"`,
        `    severity: "${r.severity}"`,
      ].join("\n");
    })
    .join("\n");

  return [
    "thresholds:",
    thresholdLines || "  {}",
    "business_rules:",
    ruleLines || "  []",
  ].join("\n");
}

export default function RuleBuilder({ onYamlChange }) {
  const [minScore, setMinScore] = useState("");
  const [rules, setRules] = useState([emptyRule()]);

  const sync = (nextRules, nextMinScore = minScore) => {
    const thresholds = {
      min_acceptable_overall_score: nextMinScore === "" ? undefined : nextMinScore,
    };
    onYamlChange(buildYaml(thresholds, nextRules));
  };

  const updateRule = (id, field, value) => {
    const next = rules.map((r) => (r.id === id ? { ...r, [field]: value } : r));
    setRules(next);
    sync(next);
  };

  const addRule = () => {
    const next = [...rules, emptyRule()];
    setRules(next);
    sync(next);
  };

  const removeRule = (id) => {
    const next = rules.filter((r) => r.id !== id);
    setRules(next.length ? next : [emptyRule()]);
    sync(next.length ? next : [emptyRule()]);
  };

  return (
    <div className="space-y-5">
      <div>
        <p className="field-label">Minimum acceptable overall score</p>
        <input
          type="number"
          min="0"
          max="100"
          value={minScore}
          onChange={(e) => {
            setMinScore(e.target.value);
            sync(rules, e.target.value);
          }}
          placeholder="e.g. 60"
          className="field-input font-mono text-sm w-40"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="field-label mb-0">Business rules</p>
          <button type="button" onClick={addRule} className="btn-secondary text-xs px-3 py-1.5">
            + Add rule
          </button>
        </div>

        <div className="space-y-3">
          {rules.map((r, i) => (
            <div key={r.id} className="rounded-lg border border-ink-600 bg-ink-800/60 p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-mono text-mist-400">Rule {i + 1}</span>
                {rules.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeRule(r.id)}
                    className="text-xs text-rose-400 hover:text-rose-300"
                  >
                    Remove
                  </button>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="field-label text-[11px]">Rule name</label>
                  <input
                    value={r.ruleId}
                    onChange={(e) => updateRule(r.id, "ruleId", e.target.value)}
                    placeholder="e.g. no_negative_amounts"
                    className="field-input font-mono text-sm"
                  />
                </div>
                <div>
                  <label className="field-label text-[11px]">Applies to columns matching</label>
                  <input
                    value={r.columnPattern}
                    onChange={(e) => updateRule(r.id, "columnPattern", e.target.value)}
                    placeholder="e.g. *Amt* or *Price*"
                    className="field-input font-mono text-sm"
                  />
                </div>
                <div>
                  <label className="field-label text-[11px]">Flag when value</label>
                  <select
                    value={r.operator}
                    onChange={(e) => updateRule(r.id, "operator", e.target.value)}
                    className="field-input text-sm"
                  >
                    {OPERATORS.map((op) => (
                      <option key={op.value} value={op.value}>
                        {op.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="field-label text-[11px]">Severity</label>
                  <select
                    value={r.severity}
                    onChange={(e) => updateRule(r.id, "severity", e.target.value)}
                    className="field-input text-sm capitalize"
                  >
                    {SEVERITIES.map((s) => (
                      <option key={s} value={s} className="capitalize">
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <label className="field-label text-[11px]">Description (optional)</label>
                  <input
                    value={r.description}
                    onChange={(e) => updateRule(r.id, "description", e.target.value)}
                    placeholder="Shown to reviewers in the report"
                    className="field-input text-sm"
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
