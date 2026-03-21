import { useState, useEffect, useCallback, useRef } from "react";

const API = "http://localhost:8000";

// ── Design tokens ─────────────────────────────────────────────────────────────
const COLORS = {
  bg: "#0A0E1A",
  surface: "#0F1628",
  card: "#141D35",
  border: "#1E2D50",
  accent: "#00D4FF",
  accentDim: "#0090AA",
  p1: "#FF3B5C",
  p1Dim: "#3D0F18",
  p2: "#FFB020",
  p2Dim: "#3D2800",
  p3: "#00E676",
  p3Dim: "#003D1A",
  critical: "#FF3B5C",
  medium: "#FFB020",
  low: "#00E676",
  text: "#E8EDF8",
  textDim: "#6B7A9E",
  success: "#00E676",
  danger: "#FF3B5C",
};

const SEV_COLOR = { P1: COLORS.p1, P2: COLORS.p2, P3: COLORS.p3 };
const SEV_DIM = { P1: COLORS.p1Dim, P2: COLORS.p2Dim, P3: COLORS.p3Dim };
const RISK_COLOR = { Critical: COLORS.critical, Medium: COLORS.medium, Low: COLORS.low };
const PATH_LABEL = { A: "AUTO-EXECUTE", B: "APPROVAL REQUIRED", C: "SENIOR REVIEW" };

// ── Global styles injected once ───────────────────────────────────────────────
const GLOBAL_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;600;700;800&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body, #root { height: 100%; }
  body {
    background: ${COLORS.bg};
    color: ${COLORS.text};
    font-family: 'Syne', sans-serif;
    overflow: hidden;
  }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: ${COLORS.surface}; }
  ::-webkit-scrollbar-thumb { background: ${COLORS.border}; border-radius: 2px; }

  @keyframes pulse-ring {
    0%   { transform: scale(1);   opacity: 1; }
    100% { transform: scale(2.2); opacity: 0; }
  }
  @keyframes slide-in {
    from { opacity: 0; transform: translateY(-12px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes fade-in {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
  @keyframes progress-fill {
    from { width: 0%; }
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  @keyframes auto-execute {
    0%   { box-shadow: 0 0 0 0 rgba(0,212,255,0.4); }
    70%  { box-shadow: 0 0 0 20px rgba(0,212,255,0); }
    100% { box-shadow: 0 0 0 0 rgba(0,212,255,0); }
  }
  .slide-in   { animation: slide-in 0.3s ease forwards; }
  .fade-in    { animation: fade-in  0.4s ease forwards; }
  .mono       { font-family: 'JetBrains Mono', monospace; }
`;

// ── Utility components ────────────────────────────────────────────────────────

function Badge({ label, color, dim, size = "sm" }) {
  const pad = size === "lg" ? "6px 14px" : "3px 10px";
  const fs = size === "lg" ? "11px" : "10px";
  return (
    <span className="mono" style={{
      background: dim || color + "22",
      color, border: `1px solid ${color}44`,
      borderRadius: 4, padding: pad,
      fontSize: fs, fontWeight: 700, letterSpacing: "0.08em",
      whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

function ConfBar({ value, color }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
      <div style={{
        flex: 1, height: 4, background: COLORS.border,
        borderRadius: 2, overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${value}%`,
          background: color || COLORS.accent,
          borderRadius: 2,
          animation: "progress-fill 0.8s ease forwards",
        }} />
      </div>
      <span className="mono" style={{ fontSize: 11, color: COLORS.textDim, minWidth: 32 }}>
        {value}%
      </span>
    </div>
  );
}

function Spinner() {
  return (
    <div style={{
      width: 16, height: 16, border: `2px solid ${COLORS.border}`,
      borderTopColor: COLORS.accent, borderRadius: "50%",
      animation: "spin 0.7s linear infinite", display: "inline-block",
    }} />
  );
}

function LiveDot({ color }) {
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
      <span style={{
        position: "absolute", width: 8, height: 8,
        borderRadius: "50%", background: color,
        animation: "pulse-ring 1.4s ease-out infinite",
      }} />
      <span style={{
        width: 8, height: 8, borderRadius: "50%", background: color,
      }} />
    </span>
  );
}

function Card({ children, style = {} }) {
  return (
    <div style={{
      background: COLORS.card, border: `1px solid ${COLORS.border}`,
      borderRadius: 10, ...style,
    }}>{children}</div>
  );
}

function SectionHeader({ title, sub, right }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.02em" }}>{title}</div>
        {sub && <div style={{ fontSize: 12, color: COLORS.textDim, marginTop: 2 }}>{sub}</div>}
      </div>
      {right}
    </div>
  );
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  try {
    const res = await fetch(API + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    return await res.json();
  } catch (e) {
    return null;
  }
}

// ── Screen 1: Live Ticket Feed ────────────────────────────────────────────────
function TicketFeed({ onSelectTicket, selected }) {
  const [tickets, setTickets] = useState([]);
  const [stats, setStats] = useState({});
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const prevIds = useRef(new Set());

  const load = useCallback(async () => {
    const [t, s] = await Promise.all([
      apiFetch("/tickets?limit=50"),
      apiFetch("/stats"),
    ]);
    if (t?.tickets) {
      setTickets(prev => {
        const newIds = new Set(t.tickets.map(x => x.id));
        // mark truly new tickets
        t.tickets.forEach(tk => { tk._new = !prevIds.current.has(tk.id); });
        prevIds.current = newIds;
        return t.tickets;
      });
    }
    if (s) setStats(s);
    setLoading(false);
  }, []);

  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, [load]);

  const visible = filter === "all" ? tickets : tickets.filter(t => t.severity === filter);

  const statCards = [
    { label: "TOTAL", val: stats.total_tickets || 0, color: COLORS.accent },
    { label: "OPEN", val: stats.open_tickets || 0, color: COLORS.p2 },
    { label: "P1 UNRESOLVED", val: stats.p1_open || 0, color: COLORS.p1 },
    { label: "PENDING", val: stats.pending_approval || 0, color: COLORS.p2 },
    { label: "RESOLVED", val: stats.resolved || 0, color: COLORS.p3 },
    { label: "RCA DONE", val: stats.rca_completed || 0, color: COLORS.accent },
  ];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 16, padding: 24, overflow: "hidden" }}>
      {/* Stat strip */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 10 }}>
        {statCards.map(s => (
          <Card key={s.label} style={{ padding: "12px 14px" }}>
            <div className="mono" style={{ fontSize: 9, color: COLORS.textDim, letterSpacing: "0.1em" }}>{s.label}</div>
            <div style={{ fontSize: 26, fontWeight: 800, color: s.color, lineHeight: 1.1, marginTop: 4 }}>{s.val}</div>
          </Card>
        ))}
      </div>

      {/* Header + filters */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <LiveDot color={COLORS.p3} />
          <span style={{ fontSize: 13, fontWeight: 600, color: COLORS.textDim }}>LIVE FEED</span>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["all", "P1", "P2", "P3"].map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{
              padding: "4px 12px", borderRadius: 4, border: "none", cursor: "pointer",
              fontSize: 11, fontWeight: 700, fontFamily: "inherit",
              background: filter === f ? COLORS.accent : COLORS.border,
              color: filter === f ? COLORS.bg : COLORS.textDim,
              transition: "all 0.15s",
            }}>{f.toUpperCase()}</button>
          ))}
        </div>
      </div>

      {/* Ticket list */}
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
        {loading && <div style={{ textAlign: "center", padding: 40 }}><Spinner /></div>}
        {!loading && visible.length === 0 && (
          <div style={{ textAlign: "center", padding: 40, color: COLORS.textDim, fontSize: 13 }}>
            No tickets. Ingest some via demo_feed.py
          </div>
        )}
        {visible.map((t, i) => (
          <TicketRow key={t.id} ticket={t} selected={selected?.id === t.id}
            onClick={() => onSelectTicket(t)} isNew={t._new} delay={i * 30} />
        ))}
      </div>
    </div>
  );
}

function TicketRow({ ticket: t, selected, onClick, isNew, delay }) {
  const sev = t.severity || "P3";
  const color = SEV_COLOR[sev];
  const dim = SEV_DIM[sev];
  const statusColor = {
    open: COLORS.p2, pending_approval: COLORS.accent,
    resolved: COLORS.p3, default: COLORS.textDim,
  }[t.status] || COLORS.textDim;

  return (
    <div className={isNew ? "slide-in" : ""} onClick={onClick}
      style={{
        animationDelay: `${delay}ms`,
        background: selected ? COLORS.border : COLORS.card,
        border: `1px solid ${selected ? COLORS.accent : COLORS.border}`,
        borderLeft: `3px solid ${color}`,
        borderRadius: 8, padding: "12px 16px", cursor: "pointer",
        transition: "all 0.15s",
        boxShadow: selected ? `0 0 0 1px ${COLORS.accent}33` : "none",
      }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
            <Badge label={sev} color={color} dim={dim} />
            <Badge label={t.category || "General"} color={COLORS.textDim} />
            <Badge label={t.status?.replace("_", " ").toUpperCase()} color={statusColor} />
            {isNew && <Badge label="NEW" color={COLORS.accent} />}
          </div>
          <div style={{
            fontSize: 13, fontWeight: 500, color: COLORS.text,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"
          }}>
            {t.description}
          </div>
          <div className="mono" style={{ fontSize: 10, color: COLORS.textDim, marginTop: 5 }}>
            {t.id}  ·  {t.opened_at?.slice(0, 16) || "–"}
          </div>
        </div>
        <div style={{ marginLeft: 12, textAlign: "right", flexShrink: 0 }}>
          <div style={{ fontSize: 11, color: COLORS.textDim }}>▶ RCA</div>
        </div>
      </div>
    </div>
  );
}

// ── Screen 2: RCA Detail ──────────────────────────────────────────────────────
function RCADetail({ ticket, onApprove }) {
  const [rca, setRca] = useState(null);
  const [pred, setPred] = useState(null);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);

  useEffect(() => {
    if (!ticket) { setRca(null); setPred(null); return; }
    setLoading(true);
    Promise.all([
      apiFetch(`/tickets/${ticket.id}/rca/result`),
      apiFetch(`/tickets/${ticket.id}/prediction`),
    ]).then(([r, p]) => {
      setRca(r?.status !== "pending" ? r : null);
      setPred(p?.status !== "pending" ? p : null);
      setLoading(false);
    });
  }, [ticket?.id]);

  async function triggerRCA() {
    setTriggering(true);
    await apiFetch(`/tickets/${ticket.id}/rca`, { method: "POST" });
    setTimeout(async () => {
      const r = await apiFetch(`/tickets/${ticket.id}/rca/result`);
      if (r?.status !== "pending") setRca(r);
      setTriggering(false);
    }, 6000);
  }

  if (!ticket) return (
    <div style={{
      height: "100%", display: "flex", alignItems: "center",
      justifyContent: "center", flexDirection: "column", gap: 12, color: COLORS.textDim
    }}>
      <div style={{ fontSize: 40 }}>←</div>
      <div style={{ fontSize: 14 }}>Select a ticket to view RCA</div>
    </div>
  );

  const riskColor = rca ? RISK_COLOR[rca.risk_tier] || COLORS.textDim : COLORS.textDim;

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Ticket header */}
      <Card style={{ padding: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              <Badge label={ticket.severity} color={SEV_COLOR[ticket.severity]} dim={SEV_DIM[ticket.severity]} size="lg" />
              <Badge label={ticket.category} color={COLORS.accent} size="lg" />
              {pred && <Badge label={pred.predicted_incident || "Classified"} color={COLORS.textDim} size="lg" />}
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{ticket.description}</div>
            <div className="mono" style={{ fontSize: 11, color: COLORS.textDim }}>{ticket.id}</div>
          </div>
          {rca && (
            <div style={{
              textAlign: "center", padding: "12px 20px",
              background: riskColor + "15", border: `1px solid ${riskColor}44`, borderRadius: 8
            }}>
              <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em" }}>RISK TIER</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: riskColor }}>{rca.risk_tier}</div>
            </div>
          )}
        </div>

        {/* Prediction confidence */}
        {pred && (
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${COLORS.border}` }}>
            <div style={{ fontSize: 11, color: COLORS.textDim, marginBottom: 6 }}>AI PREDICTION CONFIDENCE</div>
            <ConfBar value={pred.confidence_score || 0} color={RISK_COLOR[pred.risk_tier]} />
          </div>
        )}
      </Card>

      {/* RCA section */}
      {loading && (
        <div style={{ textAlign: "center", padding: 30 }}><Spinner /></div>
      )}

      {!loading && !rca && (
        <Card style={{ padding: 24, textAlign: "center" }}>
          <div style={{ fontSize: 14, color: COLORS.textDim, marginBottom: 16 }}>
            No RCA available yet
          </div>
          <button onClick={triggerRCA} disabled={triggering} style={{
            padding: "10px 28px", background: COLORS.accent, color: COLORS.bg,
            border: "none", borderRadius: 6, fontSize: 13, fontWeight: 700,
            cursor: triggering ? "not-allowed" : "pointer", fontFamily: "inherit",
            opacity: triggering ? 0.6 : 1,
          }}>
            {triggering ? <span style={{ display: "flex", alignItems: "center", gap: 8 }}><Spinner /> Running RCA...</span> : "▶ Run RCA"}
          </button>
        </Card>
      )}

      {rca && rca.root_cause && (
        <>
          {/* Root cause */}
          <Card style={{ padding: 20 }}>
            <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 10 }}>
              ROOT CAUSE ANALYSIS
            </div>
            <div style={{ fontSize: 14, lineHeight: 1.7, color: COLORS.text, marginBottom: 14 }}>
              {rca.root_cause}
            </div>
            {rca.pattern_match && (
              <div style={{
                fontSize: 12, color: COLORS.accent, padding: "8px 12px",
                background: COLORS.accent + "10", borderRadius: 6, borderLeft: `3px solid ${COLORS.accent}`
              }}>
                Pattern: {rca.pattern_match}
              </div>
            )}
          </Card>

          {/* Similar incidents */}
          <Card style={{ padding: 20 }}>
            <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 12 }}>
              TOP-3 SIMILAR PAST INCIDENTS
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {(rca.similar_incidents || []).map((s, i) => (
                <div key={i} style={{
                  padding: "12px 14px", background: COLORS.surface,
                  borderRadius: 6, border: `1px solid ${COLORS.border}`,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <Badge label={`#${i + 1}`} color={COLORS.accent} />
                      {s.severity && <Badge label={s.severity} color={SEV_COLOR[s.severity] || COLORS.textDim} />}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <ConfBar value={Math.round(s.similarity_pct || 0)} color={COLORS.accent} />
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.text, marginBottom: 6 }}>
                    {s.description?.slice(0, 100) || "–"}
                  </div>
                  {(s.resolution || s.resolution_notes) && (
                    <div style={{ fontSize: 11, color: COLORS.p3, fontStyle: "italic" }}>
                      ✓ {(s.resolution || s.resolution_notes)?.slice(0, 120)}
                    </div>
                  )}
                  {s.mttr_hrs && (
                    <div className="mono" style={{ fontSize: 10, color: COLORS.textDim, marginTop: 4 }}>
                      MTTR: {s.mttr_hrs} hrs
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>

          {/* Recommended fix + steps */}
          <Card style={{ padding: 20 }}>
            <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 10 }}>
              RECOMMENDED FIX
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.p3, marginBottom: 12 }}>
              {rca.recommended_fix}
            </div>
            {rca.fix_steps?.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {rca.fix_steps.map((step, i) => (
                  <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    <div style={{
                      width: 20, height: 20, borderRadius: "50%",
                      background: COLORS.accent + "20", color: COLORS.accent,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 10, fontWeight: 700, flexShrink: 0,
                    }}>{i + 1}</div>
                    <div style={{ fontSize: 13, color: COLORS.textDim, lineHeight: 1.5 }}>{step}</div>
                  </div>
                ))}
              </div>
            )}
            {rca.estimated_resolution_hrs && (
              <div className="mono" style={{
                fontSize: 11, color: COLORS.textDim, marginTop: 12,
                paddingTop: 12, borderTop: `1px solid ${COLORS.border}`
              }}>
                Est. resolution: {rca.estimated_resolution_hrs} hrs
              </div>
            )}
            {rca.warnings && (
              <div style={{
                marginTop: 12, padding: "8px 12px",
                background: COLORS.p1 + "10", borderRadius: 6, borderLeft: `3px solid ${COLORS.p1}`,
                fontSize: 12, color: COLORS.p1
              }}>
                ⚠ {rca.warnings}
              </div>
            )}
          </Card>

          {/* Source citations */}
          {rca.source_citations?.length > 0 && (
            <Card style={{ padding: 16 }}>
              <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 8 }}>
                SOURCE CITATIONS
              </div>
              {rca.source_citations.map((c, i) => (
                <div key={i} className="mono" style={{
                  fontSize: 11, color: COLORS.textDim,
                  padding: "4px 0", borderBottom: i < rca.source_citations.length - 1 ? `1px solid ${COLORS.border}` : "none"
                }}>
                  [{i + 1}] {c}
                </div>
              ))}
            </Card>
          )}

          {/* Proceed to approval */}
          <button onClick={() => onApprove(ticket, rca)} style={{
            width: "100%", padding: "14px", background: riskColor,
            color: COLORS.bg, border: "none", borderRadius: 8, fontSize: 14,
            fontWeight: 800, cursor: "pointer", fontFamily: "inherit",
            letterSpacing: "0.05em",
          }}>
            PROCEED TO APPROVAL WORKFLOW →
          </button>
        </>
      )}
    </div>
  );
}

// ── Screen 3: Approval Workflow ───────────────────────────────────────────────
function ApprovalWorkflow({ ticket, rca, onComplete }) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [countdown, setCountdown] = useState(null);
  const countRef = useRef(null);

  if (!ticket) return (
    <div style={{
      height: "100%", display: "flex", alignItems: "center",
      justifyContent: "center", color: COLORS.textDim, flexDirection: "column", gap: 12
    }}>
      <div style={{ fontSize: 40 }}>⚙</div>
      <div style={{ fontSize: 14 }}>Select a ticket and run RCA first</div>
    </div>
  );

  const conf = rca?.confidence_score || 50;
  const risk = rca?.risk_tier || "Medium";
  const path = ticket.severity === "P1" || risk === "Critical" ? "C"
    : conf >= 85 && risk === "Low" ? "A" : "B";
  const riskColor = RISK_COLOR[risk] || COLORS.textDim;

  // Map RCA recommended fix to a fix_type the backend understands
  const FIX_TYPE_MAP = {
    "restart": "restart_service",
    "cache": "clear_cache",
    "scale": "scale_up",
    "rollback": "rollback_config",
    "clear": "clear_cache",
    "reboot": "restart_service",
  };
  function inferFixType(fix) {
    if (!fix) return "restart_service";
    const lower = fix.toLowerCase();
    for (const [kw, ft] of Object.entries(FIX_TYPE_MAP)) {
      if (lower.includes(kw)) return ft;
    }
    return "restart_service";
  }

  async function executeAction(action) {
    setLoading(true);
    const fixType = inferFixType(rca?.recommended_fix);
    const actionType = action === "auto" ? "AUTO"
      : action === "reject" ? "REJECT"
      : action === "senior_approve" ? "APPROVE"
      : "APPROVE";

    // Call the real execute endpoint — writes to approval_actions + executions
    const data = await apiFetch(`/tickets/${ticket.id}/execute`, {
      method: "POST",
      body: JSON.stringify({
        fix_type: fixType,
        approval_path: path,
        action_type: actionType,
        operator_id: action === "auto" ? "system" : "operator",
        operator_reason: reason || rca?.recommended_fix || "Approved and executed",
        confidence: conf,
        risk_tier: risk,
      }),
    });

    const outcome = {
      action: actionType,
      ticket_id: ticket.id,
      fix_type: fixType,
      fix_applied: rca?.recommended_fix || "Manual remediation",
      executed_by: action === "auto" ? "system" : "operator",
      execution_id: data?.execution_id || null,
      rollback_url: data?.rollback_url || null,
      outcome: data?.outcome || "success",
      memory_updated: data?.memory_updated || false,
      timestamp: new Date().toISOString(),
    };

    setResult(outcome);
    setLoading(false);
    if (onComplete) onComplete(outcome);
  }

  function startAutoCountdown() {
    setCountdown(10);
    countRef.current = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          clearInterval(countRef.current);
          executeAction("auto");
          return null;
        }
        return prev - 1;
      });
    }, 1000);
  }

  function cancelAuto() {
    clearInterval(countRef.current);
    setCountdown(null);
    setResult({ action: "cancelled", outcome: "cancelled", timestamp: new Date().toISOString() });
  }

  useEffect(() => () => clearInterval(countRef.current), []);

  // ── Result screen ────────────────────────────────────────────────────────────
  if (result) {
    const isSuccess = result.outcome === "success";
    const isCancelled = result.outcome === "cancelled";
    const color = isSuccess ? COLORS.p3 : isCancelled ? COLORS.p2 : COLORS.p1;

    return (
      <div className="fade-in" style={{
        height: "100%", display: "flex",
        flexDirection: "column", alignItems: "center", justifyContent: "center",
        padding: 40, gap: 20, textAlign: "center"
      }}>
        <div style={{ fontSize: 56 }}>
          {isSuccess ? "✅" : isCancelled ? "⏹" : "❌"}
        </div>
        <div style={{ fontSize: 24, fontWeight: 800, color }}>
          {isSuccess ? "EXECUTED SUCCESSFULLY" : isCancelled ? "ACTION CANCELLED" : "ACTION REJECTED"}
        </div>
        <Card style={{ padding: 24, width: "100%", maxWidth: 480, textAlign: "left" }}>
          <div className="mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            {Object.entries(result).filter(([k]) => k !== "rollback_url").map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 12 }}>
                <span style={{ color: COLORS.textDim, minWidth: 130 }}>{k}</span>
                <span style={{ color: COLORS.text }}>{String(v)}</span>
              </div>
            ))}
          </div>
        </Card>
        {isSuccess && (
          <div style={{
            padding: "12px 20px", background: COLORS.p3 + "15",
            border: `1px solid ${COLORS.p3}33`, borderRadius: 8,
            fontSize: 12, color: COLORS.p3
          }}>
            Resolution added to AI memory — future similar tickets will benefit
          </div>
        )}
        <div style={{ display: "flex", gap: 12 }}>
          {isSuccess && result.execution_id && (
            <button onClick={async () => {
              const rb = await apiFetch(`/executions/${result.execution_id}/rollback`, { method: "POST" });
              if (rb && !rb.detail) {
                setResult(prev => ({ ...prev, outcome: "rolled_back", action: "ROLLBACK" }));
              }
            }} style={{
              padding: "10px 24px", background: COLORS.p1 + "22", color: COLORS.p1,
              border: `1px solid ${COLORS.p1}44`, borderRadius: 6, cursor: "pointer",
              fontFamily: "inherit", fontSize: 13, fontWeight: 700,
            }}>⟲ ROLLBACK</button>
          )}
          <button onClick={() => setResult(null)} style={{
            padding: "10px 24px", background: COLORS.border, color: COLORS.text,
            border: "none", borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
            fontSize: 13, fontWeight: 600,
          }}>← Back</button>
        </div>
      </div>
    );
  }

  // ── Path A: Auto-execute ─────────────────────────────────────────────────────
  if (path === "A") return (
    <div style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20
    }}>
      <PathHeader path="A" risk={risk} conf={conf} riskColor={riskColor} />
      <Card style={{ padding: 32, width: "100%", maxWidth: 520, textAlign: "center" }}>
        <div style={{ fontSize: 13, color: COLORS.textDim, marginBottom: 20 }}>
          Confidence is high enough for autonomous execution.<br />
          A 10-second cancel window is provided for human oversight.
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.p3, marginBottom: 24 }}>
          {rca?.recommended_fix}
        </div>
        {countdown !== null ? (
          <div>
            <div style={{
              fontSize: 64, fontWeight: 800, color: COLORS.accent,
              animation: "auto-execute 1s ease infinite", fontFamily: "JetBrains Mono, monospace"
            }}>
              {countdown}
            </div>
            <div style={{ fontSize: 12, color: COLORS.textDim, margin: "8px 0 20px" }}>
              Auto-executing in {countdown}s
            </div>
            <button onClick={cancelAuto} style={{
              padding: "10px 28px", background: COLORS.p1 + "22", color: COLORS.p1,
              border: `1px solid ${COLORS.p1}44`, borderRadius: 6, fontSize: 13,
              fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
            }}>⏹ CANCEL EXECUTION</button>
          </div>
        ) : (
          <button onClick={startAutoCountdown} disabled={loading} style={{
            padding: "14px 36px", background: COLORS.accent, color: COLORS.bg,
            border: "none", borderRadius: 8, fontSize: 14, fontWeight: 800,
            cursor: loading ? "not-allowed" : "pointer", fontFamily: "inherit",
          }}>
            {loading ? <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Spinner /> Executing...
            </span> : "▶ START AUTO-EXECUTE"}
          </button>
        )}
      </Card>
    </div>
  );

  // ── Path B: Single approval ───────────────────────────────────────────────────
  if (path === "B") return (
    <div style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20
    }}>
      <PathHeader path="B" risk={risk} conf={conf} riskColor={riskColor} />
      <Card style={{ padding: 28, width: "100%", maxWidth: 520 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.text, marginBottom: 8 }}>
          Recommended Fix
        </div>
        <div style={{ fontSize: 13, color: COLORS.textDim, marginBottom: 24, lineHeight: 1.7 }}>
          {rca?.recommended_fix}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <button onClick={() => executeAction("approve")} disabled={loading} style={{
            padding: "14px", background: COLORS.p3, color: COLORS.bg,
            border: "none", borderRadius: 8, fontSize: 14, fontWeight: 800,
            cursor: loading ? "not-allowed" : "pointer", fontFamily: "inherit",
          }}>
            {loading ? <Spinner /> : "✓ APPROVE"}
          </button>
          <button onClick={() => executeAction("reject")} disabled={loading} style={{
            padding: "14px", background: COLORS.p1 + "22", color: COLORS.p1,
            border: `1px solid ${COLORS.p1}44`, borderRadius: 8, fontSize: 14,
            fontWeight: 800, cursor: loading ? "not-allowed" : "pointer", fontFamily: "inherit",
          }}>✕ REJECT</button>
        </div>
      </Card>
    </div>
  );

  // ── Path C: Mandatory review ──────────────────────────────────────────────────
  return (
    <div style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20
    }}>
      <PathHeader path="C" risk={risk} conf={conf} riskColor={riskColor} />
      <Card style={{ padding: 28, width: "100%", maxWidth: 560 }}>
        <div style={{
          padding: "12px 16px", background: COLORS.p1 + "15",
          borderRadius: 6, borderLeft: `3px solid ${COLORS.p1}`,
          fontSize: 12, color: COLORS.p1, marginBottom: 20
        }}>
          ⚠ Critical risk — written justification required before execution
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Proposed Action</div>
        <div style={{ fontSize: 13, color: COLORS.textDim, marginBottom: 20, lineHeight: 1.7 }}>
          {rca?.recommended_fix}
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
          Senior Review Justification <span style={{ color: COLORS.p1 }}>*</span>
        </div>
        <textarea value={reason} onChange={e => setReason(e.target.value)}
          placeholder="Explain why this action is safe to execute in this specific context..."
          style={{
            width: "100%", minHeight: 100, padding: "12px 14px",
            background: COLORS.surface, border: `1px solid ${reason.length > 20 ? COLORS.p3 : COLORS.border}`,
            borderRadius: 8, color: COLORS.text, fontSize: 13, fontFamily: "inherit",
            resize: "vertical", outline: "none", transition: "border-color 0.2s",
          }} />
        <div style={{ fontSize: 11, color: COLORS.textDim, marginTop: 4, marginBottom: 16 }}>
          {reason.length}/200 characters — minimum 20 required
        </div>
        <button onClick={() => executeAction("senior_approve")}
          disabled={reason.length < 20 || loading} style={{
            width: "100%", padding: "14px", background: reason.length >= 20 ? COLORS.p1 : COLORS.border,
            color: reason.length >= 20 ? "#fff" : COLORS.textDim,
            border: "none", borderRadius: 8, fontSize: 14, fontWeight: 800,
            cursor: reason.length >= 20 && !loading ? "pointer" : "not-allowed",
            fontFamily: "inherit", transition: "all 0.2s",
          }}>
          {loading ? <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
            <Spinner /> Executing with audit log...
          </span> : "EXECUTE WITH FULL AUDIT TRAIL"}
        </button>
      </Card>
    </div>
  );
}

function PathHeader({ path, risk, conf, riskColor }) {
  const labels = {
    A: { title: "PATH A — AUTO-EXECUTE", sub: "High confidence • Low risk • Human cancel window active" },
    B: { title: "PATH B — OPERATOR APPROVAL", sub: "Medium confidence • Requires single operator decision" },
    C: { title: "PATH C — MANDATORY SENIOR REVIEW", sub: "Critical risk • Written justification required" },
  };
  const { title, sub } = labels[path];
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{
        fontSize: 13, fontWeight: 800, letterSpacing: "0.08em",
        color: riskColor, marginBottom: 4
      }}>{title}</div>
      <div style={{ fontSize: 12, color: COLORS.textDim }}>{sub}</div>
      <div style={{ display: "flex", gap: 12, justifyContent: "center", marginTop: 12 }}>
        <Badge label={`RISK: ${risk}`} color={riskColor} size="lg" />
        <Badge label={`CONF: ${conf}%`} color={COLORS.accent} size="lg" />
        <Badge label={`PATH ${path}`} color={riskColor} size="lg" />
      </div>
    </div>
  );
}

// ── Screen 4: Audit Trail ─────────────────────────────────────────────────────
function AuditTrail() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("ALL");

  useEffect(() => {
    async function load() {
      const data = await apiFetch("/audit?limit=200");
      if (data?.events) setEvents(data.events);
      setLoading(false);
    }
    load();
    const i = setInterval(load, 8000);
    return () => clearInterval(i);
  }, []);

  const EVENT_TYPES = ["ALL", "INGEST", "PREDICT", "RCA", "APPROVE", "EXECUTE", "ROLLBACK", "RESOLVE"];
  const visible = filter === "ALL" ? events : events.filter(e => e.event_type === filter);

  function exportCSV() {
    if (!visible.length) return;
    const headers = Object.keys(visible[0]).join(",");
    const rows = visible.map(e =>
      Object.values(e).map(v => `"${String(v || "").replace(/"/g, '""')}"`).join(",")
    );
    const csv = [headers, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `opsai_audit_${Date.now()}.csv`;
    a.click(); URL.revokeObjectURL(url);
  }

  const EVENT_COLOR = {
    INGEST: COLORS.textDim, PREDICT: COLORS.accent, RCA: "#A78BFA",
    APPROVE: COLORS.p3, EXECUTE: COLORS.p3, REJECT: COLORS.p1,
    ROLLBACK: COLORS.p2, RESOLVE: COLORS.p3, OVERRIDE: COLORS.p1,
  };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", padding: 24, gap: 16, overflow: "hidden" }}>
      <SectionHeader
        title="Audit Trail"
        sub={`${events.length} events · Immutable · Exportable`}
        right={
          <button onClick={exportCSV} style={{
            padding: "8px 18px", background: COLORS.accent, color: COLORS.bg,
            border: "none", borderRadius: 6, fontSize: 12, fontWeight: 700,
            cursor: "pointer", fontFamily: "inherit",
          }}>⬇ EXPORT CSV</button>
        }
      />

      {/* Event type filters */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {EVENT_TYPES.map(t => (
          <button key={t} onClick={() => setFilter(t)} style={{
            padding: "4px 10px", borderRadius: 4, border: "none", cursor: "pointer",
            fontSize: 10, fontWeight: 700, fontFamily: "JetBrains Mono, monospace",
            background: filter === t ? (EVENT_COLOR[t] || COLORS.accent) : COLORS.border,
            color: filter === t ? COLORS.bg : COLORS.textDim,
          }}>{t}</button>
        ))}
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {loading && <div style={{ textAlign: "center", padding: 30 }}><Spinner /></div>}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ position: "sticky", top: 0, background: COLORS.surface }}>
              {["TIMESTAMP", "EVENT", "TICKET", "OPERATOR", "PATH", "CONFIDENCE", "RISK", "ACTION", "OUTCOME"].map(h => (
                <th key={h} className="mono" style={{
                  padding: "8px 10px", textAlign: "left", fontSize: 9,
                  color: COLORS.textDim, letterSpacing: "0.08em",
                  borderBottom: `1px solid ${COLORS.border}`,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((e, i) => {
              const eColor = EVENT_COLOR[e.event_type] || COLORS.textDim;
              return (
                <tr key={e.id || i} style={{
                  borderBottom: `1px solid ${COLORS.border}22`,
                  transition: "background 0.1s",
                }} onMouseEnter={el => el.currentTarget.style.background = COLORS.border + "44"}
                  onMouseLeave={el => el.currentTarget.style.background = "transparent"}>
                  <td className="mono" style={{ padding: "8px 10px", color: COLORS.textDim, fontSize: 10, whiteSpace: "nowrap" }}>
                    {e.timestamp?.slice(0, 16) || "–"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <Badge label={e.event_type} color={eColor} />
                  </td>
                  <td className="mono" style={{ padding: "8px 10px", color: COLORS.accent, fontSize: 10 }}>
                    {e.ticket_id?.slice(0, 14) || "–"}
                  </td>
                  <td className="mono" style={{ padding: "8px 10px", color: COLORS.textDim, fontSize: 10 }}>
                    {e.operator_id || "system"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {e.approval_path && <Badge label={`Path ${e.approval_path}`} color={COLORS.textDim} />}
                  </td>
                  <td style={{ padding: "8px 10px", minWidth: 80 }}>
                    {e.confidence != null && <ConfBar value={e.confidence} color={RISK_COLOR[e.risk_tier]} />}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {e.risk_tier && <Badge label={e.risk_tier} color={RISK_COLOR[e.risk_tier] || COLORS.textDim} />}
                  </td>
                  <td style={{
                    padding: "8px 10px", maxWidth: 200, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap", color: COLORS.text, fontSize: 11
                  }}>
                    {e.action_taken || e.reasoning?.slice(0, 60) || "–"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {e.outcome && (
                      <Badge label={e.outcome}
                        color={e.outcome === "success" || e.outcome === "resolved" || e.outcome === "created"
                          ? COLORS.p3 : e.outcome === "rollback" ? COLORS.p2 : COLORS.textDim} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!loading && visible.length === 0 && (
          <div style={{ textAlign: "center", padding: 40, color: COLORS.textDim }}>No events</div>
        )}
      </div>
    </div>
  );
}
// ── Add this component to your App.jsx ───────────────────────────────────────
// Place it after the AuditTrail component, before the Main App Shell section

function IngestForm({ onIngested }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [form, setForm] = useState({
    description: "",
    ci_cat: "storage",
    ci_subcat: "",
    urgency: "3",
    impact: "3",
    alert_status: "False",
    source: "manual",
  });

  // Quick-fill presets for demo
  const PRESETS = [
    {
      label: "🔴 P1 DB Outage",
      data: {
        description: "Production database completely unresponsive — all users locked out, entire application down",
        ci_cat: "storage", urgency: "1", impact: "1", alert_status: "True",
      }
    },
    {
      label: "🔴 P1 Security",
      data: {
        description: "Unauthorized access detected on payment processing server, possible data breach in progress",
        ci_cat: "application", urgency: "1", impact: "1", alert_status: "True",
      }
    },
    {
      label: "🟡 P2 Network",
      data: {
        description: "Intermittent packet loss on payment network segment, some transactions timing out",
        ci_cat: "network", urgency: "2", impact: "2", alert_status: "False",
      }
    },
    {
      label: "🟡 P2 App Slow",
      data: {
        description: "Web based application responding slowly, users reporting timeout errors on checkout",
        ci_cat: "subapplication", urgency: "2", impact: "3", alert_status: "False",
      }
    },
    {
      label: "🟢 P3 Certificate",
      data: {
        description: "SSL certificate expiring in 14 days on internal monitoring dashboard",
        ci_cat: "", urgency: "4", impact: "4", alert_status: "False",
      }
    },
  ];

  async function submit() {
    if (!form.description.trim()) return;
    setLoading(true);
    setResult(null);
    const data = await apiFetch("/tickets/ingest", {
      method: "POST",
      body: JSON.stringify(form),
    });
    setLoading(false);
    if (data) {
      setResult(data);
      if (onIngested) onIngested(data);
      // Reset description only, keep other fields
      setForm(f => ({ ...f, description: "" }));
    }
  }

  const CI_CATS = ["storage", "application", "subapplication", "network", "hardware", ""];

  return (
    <>
      {/* Floating trigger button */}
      <button onClick={() => setOpen(o => !o)} style={{
        position: "fixed", bottom: 40, right: 40,
        width: 52, height: 52, borderRadius: "50%",
        background: COLORS.accent, color: COLORS.bg,
        border: "none", cursor: "pointer", fontSize: 22,
        boxShadow: `0 0 0 0 ${COLORS.accent}`,
        animation: "auto-execute 2s ease infinite",
        zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center",
        fontWeight: 800,
      }} title="Ingest new ticket">
        {open ? "✕" : "+"}
      </button>

      {/* Modal */}
      {open && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)",
          zIndex: 99, display: "flex", alignItems: "center", justifyContent: "center",
          animation: "fade-in 0.2s ease",
        }} onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div style={{
            width: 560, background: COLORS.card,
            border: `1px solid ${COLORS.border}`, borderRadius: 12,
            padding: 28, animation: "slide-in 0.25s ease",
          }}>
            <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 6 }}>
              Ingest New Ticket
            </div>
            <div style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 20 }}>
              Submits to live API → triggers AI prediction + RCA automatically
            </div>

            {/* Quick presets */}
            <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 8 }}>
              QUICK PRESETS
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 20 }}>
              {PRESETS.map(p => (
                <button key={p.label} onClick={() => setForm(f => ({ ...f, ...p.data }))} style={{
                  padding: "5px 12px", borderRadius: 4, border: `1px solid ${COLORS.border}`,
                  background: COLORS.surface, color: COLORS.text, fontSize: 11,
                  cursor: "pointer", fontFamily: "inherit", fontWeight: 600,
                }}>{p.label}</button>
              ))}
            </div>

            {/* Description */}
            <div style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 6 }}>DESCRIPTION *</div>
            <textarea
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Describe the incident in plain English..."
              rows={3}
              style={{
                width: "100%", padding: "10px 12px", marginBottom: 16,
                background: COLORS.surface, border: `1px solid ${COLORS.border}`,
                borderRadius: 6, color: COLORS.text, fontSize: 13,
                fontFamily: "inherit", resize: "vertical", outline: "none",
              }}
            />

            {/* Fields row */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10, marginBottom: 20 }}>
              {[
                { key: "ci_cat", label: "CI CATEGORY", type: "select", options: CI_CATS },
                { key: "urgency", label: "URGENCY", type: "select", options: ["1", "2", "3", "4"] },
                { key: "impact", label: "IMPACT", type: "select", options: ["1", "2", "3", "4"] },
                { key: "alert_status", label: "ALERT", type: "select", options: ["False", "True"] },
              ].map(field => (
                <div key={field.key}>
                  <div className="mono" style={{ fontSize: 9, color: COLORS.textDim, marginBottom: 4, letterSpacing: "0.08em" }}>
                    {field.label}
                  </div>
                  <select
                    value={form[field.key]}
                    onChange={e => setForm(f => ({ ...f, [field.key]: e.target.value }))}
                    style={{
                      width: "100%", padding: "6px 8px",
                      background: COLORS.surface, border: `1px solid ${COLORS.border}`,
                      borderRadius: 4, color: COLORS.text, fontSize: 12,
                      fontFamily: "inherit", outline: "none", cursor: "pointer",
                    }}
                  >
                    {field.options.map(o => (
                      <option key={o} value={o}>{o || "(none)"}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>

            {/* Result */}
            {result && (
              <div style={{
                padding: "10px 14px", borderRadius: 6, marginBottom: 16,
                background: COLORS.p3 + "15", border: `1px solid ${COLORS.p3}33`,
              }}>
                <div className="mono" style={{ fontSize: 11, color: COLORS.p3 }}>
                  ✓ Ingested: {result.id} · Severity: {result.severity} · {result.message?.slice(0, 60)}
                </div>
                {result.anomaly_flags?.length > 0 && (
                  <div className="mono" style={{ fontSize: 10, color: COLORS.p2, marginTop: 4 }}>
                    ⚠ Anomaly flags: {result.anomaly_flags.join(", ")}
                  </div>
                )}
              </div>
            )}

            {/* Buttons */}
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={submit} disabled={!form.description.trim() || loading} style={{
                flex: 1, padding: "12px", background: form.description.trim() ? COLORS.accent : COLORS.border,
                color: form.description.trim() ? COLORS.bg : COLORS.textDim,
                border: "none", borderRadius: 6, fontSize: 13, fontWeight: 800,
                cursor: form.description.trim() && !loading ? "pointer" : "not-allowed",
                fontFamily: "inherit",
              }}>
                {loading
                  ? <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}><Spinner /> Ingesting...</span>
                  : "▶ INGEST TICKET"}
              </button>
              <button onClick={() => setOpen(false)} style={{
                padding: "12px 20px", background: COLORS.surface, color: COLORS.textDim,
                border: `1px solid ${COLORS.border}`, borderRadius: 6,
                fontSize: 13, cursor: "pointer", fontFamily: "inherit",
              }}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Main App Shell ────────────────────────────────────────────────────────────
export default function App() {
  const [screen, setScreen] = useState("feed");
  const [selected, setSelected] = useState(null);
  const [rcaData, setRcaData] = useState(null);
  const [apiOk, setApiOk] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = GLOBAL_CSS;
    document.head.appendChild(style);
    return () => document.head.removeChild(style);
  }, []);

  useEffect(() => {
    apiFetch("/health").then(d => setApiOk(d?.status === "ok"));
  }, []);

  function handleSelectTicket(ticket) {
    setSelected(ticket);
    setScreen("rca");
  }

  function handleApprove(ticket, rca) {
    setSelected(ticket);
    setRcaData(rca);
    setScreen("approval");
  }

  const NAV = [
    { id: "feed", label: "01  LIVE FEED", icon: "⬡" },
    { id: "rca", label: "02  RCA DETAIL", icon: "⬡" },
    { id: "approval", label: "03  APPROVAL", icon: "⬡" },
    { id: "audit", label: "04  AUDIT TRAIL", icon: "⬡" },
  ];

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: COLORS.bg }}>
      {/* Top bar */}
      <div style={{
        height: 52, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 24px", borderBottom: `1px solid ${COLORS.border}`,
        background: COLORS.surface, flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontWeight: 800, fontSize: 15, letterSpacing: "-0.02em" }}>
            <span style={{ color: COLORS.accent }}>TAME</span>
            <span style={{ color: COLORS.textDim }}>PND</span>
          </div>
          <div className="mono" style={{
            fontSize: 9, color: COLORS.textDim,
            letterSpacing: "0.12em", padding: "3px 8px", border: `1px solid ${COLORS.border}`,
            borderRadius: 3
          }}>
            HUMAN-GOVERNED AIOPS
          </div>
        </div>

        <div style={{ display: "flex", gap: 4 }}>
          {NAV.map(n => (
            <button key={n.id} onClick={() => setScreen(n.id)} style={{
              padding: "6px 16px", background: screen === n.id ? COLORS.accent + "15" : "transparent",
              border: `1px solid ${screen === n.id ? COLORS.accent : "transparent"}`,
              borderRadius: 4, color: screen === n.id ? COLORS.accent : COLORS.textDim,
              fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
              letterSpacing: "0.04em", transition: "all 0.15s",
            }}>{n.label}</button>
          ))}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <LiveDot color={apiOk === null ? COLORS.textDim : apiOk ? COLORS.p3 : COLORS.p1} />
          <span className="mono" style={{ fontSize: 10, color: COLORS.textDim }}>
            {apiOk === null ? "CONNECTING" : apiOk ? "API LIVE" : "API OFFLINE"}
          </span>
        </div>
      </div>

      {/* Main content */}
      <div style={{
        flex: 1, overflow: "hidden", display: "grid",
        gridTemplateColumns: screen === "feed" ? "1fr 1fr" : "1fr",
        gridTemplateRows: "1fr",
      }}>
        {screen === "feed" && (
          <>
            <div style={{ borderRight: `1px solid ${COLORS.border}`, overflow: "hidden" }}>
              <TicketFeed key={refreshKey} onSelectTicket={handleSelectTicket} selected={selected} />
            </div>
            <div style={{ overflow: "hidden" }}>
              <RCADetail ticket={selected} onApprove={handleApprove} />
            </div>
          </>
        )}
        {screen === "rca" && (
          <RCADetail ticket={selected} onApprove={handleApprove} />
        )}
        {screen === "approval" && (
          <ApprovalWorkflow ticket={selected} rca={rcaData}
            onComplete={() => setTimeout(() => setScreen("audit"), 2000)} />
        )}
        {screen === "audit" && <AuditTrail />}
      </div>

      {/* Status bar */}
      <div style={{
        height: 28, display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 24px", borderTop: `1px solid ${COLORS.border}`,
        background: COLORS.surface, flexShrink: 0,
      }}>
        <div className="mono" style={{ fontSize: 9, color: COLORS.textDim }}>
          PHASE 1+2+3 · GROQ LLAMA-3.3-70B · FAISS · SQLITE
        </div>
        <div className="mono" style={{ fontSize: 9, color: COLORS.textDim }}>
          {selected ? `SELECTED: ${selected.id}` : "NO TICKET SELECTED"}
        </div>
        <div className="mono" style={{ fontSize: 9, color: COLORS.textDim }}>
          {new Date().toLocaleTimeString()}
        </div>
      </div>
      <IngestForm onIngested={() => setRefreshKey(k => k + 1)} />
    </div>
  );
}
