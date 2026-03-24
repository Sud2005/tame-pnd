import { useState, useEffect, useCallback, useRef } from "react";

const API = "http://localhost:8000";

// ── Design tokens ─────────────────────────────────────────────────────────────
const COLORS = {
  bg:       "#0A0E1A",
  surface:  "#0F1628",
  card:     "#141D35",
  border:   "#1E2D50",
  accent:   "#00D4FF",
  accentDim:"#0090AA",
  p1:       "#FF3B5C",
  p1Dim:    "#3D0F18",
  p2:       "#FFB020",
  p2Dim:    "#3D2800",
  p3:       "#00E676",
  p3Dim:    "#003D1A",
  critical: "#FF3B5C",
  medium:   "#FFB020",
  low:      "#00E676",
  text:     "#E8EDF8",
  textDim:  "#6B7A9E",
  success:  "#00E676",
  danger:   "#FF3B5C",
};

const SEV_COLOR  = { P1: COLORS.p1,  P2: COLORS.p2,  P3: COLORS.p3  };
const SEV_DIM    = { P1: COLORS.p1Dim,P2: COLORS.p2Dim,P3: COLORS.p3Dim };
const RISK_COLOR = { Critical: COLORS.critical, Medium: COLORS.medium, Low: COLORS.low };
const PATH_LABEL = { A: "AUTO-EXECUTE", B: "APPROVAL REQUIRED", C: "SENIOR REVIEW" };
const STATUS_COLOR = {
  open: COLORS.p2, pending_approval: COLORS.accent, resolved: COLORS.p3,
  rolled_back: "#FF6B35", rejected: "#CC2244", reraised: "#7B61FF",
};
const STATUS_LABEL = {
  open: "OPEN", pending_approval: "PENDING", resolved: "RESOLVED",
  rolled_back: "ROLLED BACK", rejected: "REJECTED", reraised: "RE-RAISED",
};

// Map recommended_fix text to a simulation fix_type (mirrors backend _map_fix_to_type)
function mapFixType(text) {
  const t = (text || "").toLowerCase();
  if (["restart","reboot","service"].some(k => t.includes(k))) return "restart_service";
  if (["cache","clear","purge","flush"].some(k => t.includes(k))) return "clear_cache";
  if (["scale","replica","capacity","horizontal"].some(k => t.includes(k))) return "scale_up";
  if (["rollback","revert","undo","previous","config"].some(k => t.includes(k))) return "rollback_config";
  return "restart_service";
}

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
  const fs  = size === "lg" ? "11px" : "10px";
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
    const data = await res.json();
    if (!res.ok) {
      console.warn(`API ${res.status} ${path}:`, data);
      return { _error: true, status: res.status, detail: data?.detail || "Server error", ...data };
    }
    return data;
  } catch (e) {
    console.error(`API fetch error ${path}:`, e);
    return null;
  }
}

// ── Screen 1: Live Ticket Feed ────────────────────────────────────────────────
function TicketFeed({ onSelectTicket, selected }) {
  const [tickets, setTickets]   = useState([]);
  const [stats, setStats]       = useState({});
  const [loading, setLoading]   = useState(true);
  const [filter, setFilter]     = useState("all");
  const prevIds = useRef(new Set());

  const load = useCallback(async () => {
    const sevParam = filter !== "all" ? `&severity=${filter}` : "";
    const [t, s] = await Promise.all([
      apiFetch(`/tickets?limit=50&exclude_resolved=true${sevParam}`),
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
  }, [filter]);

  useEffect(() => {
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, [load, filter]);

  const visible = tickets; // filtering done server-side via API params

  const statCards = [
    { label: "OPEN",     val: stats.open_tickets    || 0, color: COLORS.p2 },
    { label: "PENDING",  val: stats.pending_approval|| 0, color: COLORS.accent },
    { label: "RESOLVED", val: stats.resolved        || 0, color: COLORS.p3 },
    { label: "RCA DONE", val: stats.rca_completed   || 0, color: COLORS.accent },
  ];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", gap: 16, padding: 24, overflow: "hidden" }}>
      {/* Stat strip */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
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
          {["all","P1","P2","P3"].map(f => (
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
  const sev   = t.severity || "P3";
  const color = SEV_COLOR[sev];
  const dim   = SEV_DIM[sev];
  const statusColor = STATUS_COLOR[t.status] || COLORS.textDim;

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
            <Badge label={STATUS_LABEL[t.status] || t.status?.replace("_"," ").toUpperCase()} color={statusColor} />
            {isNew && <Badge label="NEW" color={COLORS.accent} />}
          </div>
          <div style={{ fontSize: 13, fontWeight: 500, color: COLORS.text,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {t.description}
          </div>
          <div className="mono" style={{ fontSize: 10, color: COLORS.textDim, marginTop: 5 }}>
            {t.id}  ·  {t.opened_at?.slice(0,16) || "–"}
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
  const [rca,  setRca]  = useState(null);
  const [pred, setPred] = useState(null);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);

  const [executions, setExecutions] = useState([]);

  useEffect(() => {
    if (!ticket) { setRca(null); setPred(null); setExecutions([]); return; }
    setLoading(true);
    Promise.all([
      apiFetch(`/tickets/${ticket.id}/rca/result`),
      apiFetch(`/tickets/${ticket.id}/prediction`),
      apiFetch(`/tickets/${ticket.id}/executions`),
    ]).then(([r, p, ex]) => {
      setRca(r?.status !== "pending" ? r : null);
      setPred(p?.status !== "pending" ? p : null);
      setExecutions(ex?.executions || []);
      setLoading(false);
    });
  }, [ticket?.id]);

  async function triggerRCA() {
    setTriggering(true);
    await apiFetch(`/tickets/${ticket.id}/rca`, { method: "POST" });
    // Poll for result every 3s, up to 10 attempts (30s total)
    let attempts = 0;
    const maxAttempts = 10;
    const poll = setInterval(async () => {
      attempts++;
      const r = await apiFetch(`/tickets/${ticket.id}/rca/result`);
      if (r && r.status !== "pending") {
        clearInterval(poll);
        setRca(r);
        setTriggering(false);
      } else if (attempts >= maxAttempts) {
        clearInterval(poll);
        setTriggering(false);
        // Show whatever we got (even fallback)
        if (r) setRca(r);
      }
    }, 3000);
  }

  if (!ticket) return (
    <div style={{ height: "100%", display: "flex", alignItems: "center",
      justifyContent: "center", flexDirection: "column", gap: 12, color: COLORS.textDim }}>
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
            <div style={{ textAlign: "center", padding: "12px 20px",
              background: riskColor + "15", border: `1px solid ${riskColor}44`, borderRadius: 8 }}>
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
              <div style={{ fontSize: 12, color: COLORS.accent, padding: "8px 12px",
                background: COLORS.accent + "10", borderRadius: 6, borderLeft: `3px solid ${COLORS.accent}` }}>
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
                      <Badge label={`#${i+1}`} color={COLORS.accent} />
                      {s.severity && <Badge label={s.severity} color={SEV_COLOR[s.severity] || COLORS.textDim} />}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <ConfBar value={Math.round(s.similarity_pct || 0)} color={COLORS.accent} />
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: COLORS.text, marginBottom: 6 }}>
                    {s.description?.slice(0,100) || "–"}
                  </div>
                  {(s.resolution || s.resolution_notes) && (
                    <div style={{ fontSize: 11, color: COLORS.p3, fontStyle: "italic" }}>
                      ✓ {(s.resolution || s.resolution_notes)?.slice(0,120)}
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
                    }}>{i+1}</div>
                    <div style={{ fontSize: 13, color: COLORS.textDim, lineHeight: 1.5 }}>{step}</div>
                  </div>
                ))}
              </div>
            )}
            {rca.estimated_resolution_hrs && (
              <div className="mono" style={{ fontSize: 11, color: COLORS.textDim, marginTop: 12,
                paddingTop: 12, borderTop: `1px solid ${COLORS.border}` }}>
                Est. resolution: {rca.estimated_resolution_hrs} hrs
              </div>
            )}
            {rca.warnings && (
              <div style={{ marginTop: 12, padding: "8px 12px",
                background: COLORS.p1 + "10", borderRadius: 6, borderLeft: `3px solid ${COLORS.p1}`,
                fontSize: 12, color: COLORS.p1 }}>
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
                <div key={i} className="mono" style={{ fontSize: 11, color: COLORS.textDim,
                  padding: "4px 0", borderBottom: i < rca.source_citations.length-1 ? `1px solid ${COLORS.border}` : "none" }}>
                  [{i+1}] {c}
                </div>
              ))}
            </Card>
          )}

          {/* Proceed to approval */}
          <button onClick={() => onApprove(ticket, rca, pred)} style={{
            width: "100%", padding: "14px", background: riskColor,
            color: COLORS.bg, border: "none", borderRadius: 8, fontSize: 14,
            fontWeight: 800, cursor: "pointer", fontFamily: "inherit",
            letterSpacing: "0.05em",
          }}>
            PROCEED TO APPROVAL WORKFLOW →
          </button>

          {/* Execution history */}
          {executions.length > 0 && (
            <ExecutionHistory executions={executions} ticketId={ticket.id}
              onRefresh={() => {
                apiFetch(`/tickets/${ticket.id}/executions`).then(d => setExecutions(d?.executions || []));
              }}
            />)}

          {/* Reraise panel for rolled_back / rejected tickets */}
          {(ticket.status === "rolled_back" || ticket.status === "rejected") && (
            <ReraisePanel ticket={ticket} />)}
        </>
      )}
    </div>
  );
}

// ── Screen 3: Approval Workflow ───────────────────────────────────────────────
function ApprovalWorkflow({ ticket, rca, pred, onComplete }) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState(null);
  const [countdown, setCountdown] = useState(null);
  const [navCountdown, setNavCountdown] = useState(null);
  const countRef = useRef(null);
  const navRef   = useRef(null);
  const onCompleteFired = useRef(false);

  function fireOnComplete(resultData) {
    if (!onCompleteFired.current && onComplete) {
      onCompleteFired.current = true;
      onComplete(resultData);
    }
  }

  if (!ticket) return (
    <div style={{ height: "100%", display: "flex", alignItems: "center",
      justifyContent: "center", color: COLORS.textDim, flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 40 }}>⚙</div>
      <div style={{ fontSize: 14 }}>Select a ticket and run RCA first</div>
    </div>
  );

  // Use prediction confidence if available — same value shown in RCA Overview
  const conf      = pred?.confidence_score ?? rca?.confidence_score ?? 50;
  const risk      = pred?.risk_tier || rca?.risk_tier || "Medium";

  // Priority: use backend-calculated path from prediction (most accurate)
  // Falls back to rca path, then client-side heuristic
  const storedPath = pred?.approval_path || rca?.approval_path;
  const path = storedPath || (
    ticket.severity === "P1"                          ? "C" :
    ticket.severity === "P3" && conf >= 70             ? "A" :
    ticket.severity === "P2" && conf >= 85 && risk !== "Critical" ? "A" :
    ticket.severity === "P3" && conf >= 40             ? "B" :
    ticket.severity === "P2" && conf >= 50             ? "B" :
    conf < 40                                          ? "C" :
    "C"
  );
  const riskColor = RISK_COLOR[risk] || COLORS.textDim;

  const [rejectModal, setRejectModal] = useState(false);

  async function executeAction(action, reason = "") {
    setLoading(true);

    let outcome = "success";
    const fixType = mapFixType(rca?.recommended_fix);

    try {
      if (action === "reject") {
        // Use v2 reject endpoint with approval_actions trail
        const res = await apiFetch(`/tickets/${ticket.id}/reject_v2`, {
          method: "POST",
          body: JSON.stringify({
            operator_id: "ops_dashboard",
            reject_reason: reason || "Fix recommendation rejected by operator",
            approval_path: path,
            rca_id: rca?.id || null,
          }),
        });
        if (!res || res._error) {
          outcome = "error";
        } else {
          outcome = (res.status === "rejected" || res.ticket_status === "rejected") ? "rejected" : "error";
        }

      } else if (action === "rollback") {
        const res = await apiFetch(`/tickets/${ticket.id}/rollback`, {
          method: "POST",
          body: JSON.stringify({
            reason: reason || "Fix rolled back by operator",
            rolled_back_by: "ops_dashboard",
          }),
        });
        if (!res || res._error) {
          outcome = "error";
        } else {
          outcome = res.outcome || res.ticket_status || "rolled_back";
        }

      } else {
        // approve / auto / senior_approve — call /execute endpoint
        const apiResult = await apiFetch(`/tickets/${ticket.id}/execute`, {
          method: "POST",
          body: JSON.stringify({
            fix_type: fixType,
            operator_id: "ops_dashboard",
            operator_reason: reason || rca?.recommended_fix || "Fix approved and executed",
            approval_path: path,
            rca_id: rca?.id || null,
          }),
        });
        if (!apiResult || apiResult._error) {
          outcome = "error";
        } else {
          outcome = apiResult.outcome || "success";
        }
      }
    } catch (err) {
      console.error("executeAction error:", err);
      outcome = "error";
    }

    const resultData = {
      action,
      ticket_id:   ticket.id,
      fix_applied: outcome === "rejected" ? "N/A — fix rejected" :
                   outcome === "rolled_back" ? "Rolled back to pre-execution state" :
                   rca?.recommended_fix || "Manual remediation",
      fix_type:    fixType,
      executed_by: action === "auto" ? "system" : "ops_dashboard",
      timestamp:   new Date().toISOString(),
      outcome,
      rollback_available: outcome === "success",
    };

    setResult(resultData);
    onCompleteFired.current = false; // reset guard for this new result
    setLoading(false);
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

  // ── Auto-navigate to audit trail after rejection or rollback ────────────────
  useEffect(() => {
    const outcome = result?.outcome;
    if (!outcome || outcome === "cancelled" || outcome === "awaiting_rollback" || outcome === "error") return;
    const delay = (outcome === "rejected" || outcome === "rolled_back") ? 3 : 5;
    setNavCountdown(delay);
    navRef.current = setInterval(() => {
      setNavCountdown(prev => {
        if (prev <= 1) {
          clearInterval(navRef.current);
          fireOnComplete(result);
          if (typeof window.__setScreen === "function") window.__setScreen("audit");
          return null;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(navRef.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result?.outcome]);


  if (result) {
    const isSuccess   = result.outcome === "success";
    const isRejected  = result.outcome === "rejected";
    const isRolledBack= result.outcome === "rolled_back";
    const isCancelled = result.outcome === "cancelled";
    const isError     = result.outcome === "error";

    const icon  = isSuccess ? "✅" : isRejected ? "🚫" : isRolledBack ? "↩️" : isCancelled ? "⏹" : isError ? "❌" : "❌";
    const label = isSuccess    ? "EXECUTED SUCCESSFULLY"
                : isRejected   ? "FIX REJECTED — TICKET STAYS OPEN"
                : isRolledBack ? "FIX ROLLED BACK — REVERTED"
                : isCancelled  ? "ACTION CANCELLED"
                : isError      ? "ACTION FAILED — API ERROR"
                : "ACTION FAILED";
    const color = isSuccess ? COLORS.p3 : isRejected ? COLORS.p1 : isRolledBack ? COLORS.p2 : isError ? COLORS.p1 : COLORS.p2;

    return (
      <div className="fade-in" style={{ height: "100%", display: "flex",
        flexDirection: "column", alignItems: "center", justifyContent: "center",
        padding: 40, gap: 20, textAlign: "center" }}>
        <div style={{ fontSize: 56 }}>{icon}</div>
        <div style={{ fontSize: 22, fontWeight: 800, color }}>{label}</div>

        <Card style={{ padding: 24, width: "100%", maxWidth: 500, textAlign: "left" }}>
          <div className="mono" style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            {Object.entries(result).map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 12 }}>
                <span style={{ color: COLORS.textDim, minWidth: 140 }}>{k}</span>
                <span style={{ color: COLORS.text }}>{String(v)}</span>
              </div>
            ))}
          </div>
        </Card>

        {isSuccess && (
          <div style={{ padding: "10px 16px", background: COLORS.p3 + "15",
            border: `1px solid ${COLORS.p3}33`, borderRadius: 8, fontSize: 12, color: COLORS.p3 }}>
            ✓ Resolution added to AI memory — future similar tickets will benefit
          </div>
        )}
        {(isRejected || isRolledBack) && (
          <div style={{ padding: "10px 16px", background: COLORS.p2 + "15",
            border: `1px solid ${COLORS.p2}33`, borderRadius: 8, fontSize: 12, color: COLORS.p2 }}>
            ⚠ Ticket remains open — escalate manually or trigger a new RCA
          </div>
        )}
        {isError && (
          <div style={{ padding: "10px 16px", background: COLORS.p1 + "15",
            border: `1px solid ${COLORS.p1}33`, borderRadius: 8, fontSize: 12, color: COLORS.p1 }}>
            ✗ API request failed. Ticket status has NOT changed. Please retry.
          </div>
        )}
        {isSuccess && (
          <button onClick={() => {
            clearInterval(navRef.current);
            setNavCountdown(null);
            setResult({ ...result, outcome: "awaiting_rollback" });
          }} style={{
            padding: "8px 20px", background: COLORS.p2 + "22", color: COLORS.p2,
            border: `1px solid ${COLORS.p2}44`, borderRadius: 6,
            fontSize: 12, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
          }}>↩ Rollback This Fix</button>
        )}
        {result.outcome === "awaiting_rollback" && (
          <button onClick={() => executeAction("rollback")} disabled={loading} style={{
            padding: "10px 24px", background: COLORS.p1, color: "#fff",
            border: "none", borderRadius: 6, fontSize: 13, fontWeight: 800,
            cursor: "pointer", fontFamily: "inherit",
          }}>
            {loading ? <Spinner /> : "↩ CONFIRM ROLLBACK"}
          </button>
        )}

        {/* Auto-navigate countdown indicator */}
        {navCountdown != null && !isCancelled && !isError && result.outcome !== "awaiting_rollback" && (
          <div style={{ padding: "8px 16px", background: COLORS.accent + "15",
            border: `1px solid ${COLORS.accent}33`, borderRadius: 8, fontSize: 12, color: COLORS.accent }}>
            ↪ Moving to Audit Trail in {navCountdown}s…
            <button onClick={() => { clearInterval(navRef.current); setNavCountdown(null); }} style={{
              marginLeft: 10, padding: "2px 8px", background: "transparent",
              border: `1px solid ${COLORS.accent}66`, borderRadius: 4, color: COLORS.accent,
              fontSize: 11, cursor: "pointer", fontFamily: "inherit",
            }}>Cancel</button>
          </div>
        )}

        <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
          <button onClick={() => { clearInterval(navRef.current); setNavCountdown(null); setResult(null); }} style={{
            padding: "10px 20px", background: COLORS.border, color: COLORS.text,
            border: "none", borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
            fontSize: 13, fontWeight: 600,
          }}>← Back to Workflow</button>
          <button onClick={() => {
            clearInterval(navRef.current);
            setNavCountdown(null);
            fireOnComplete(result);
            if (typeof window.__setScreen === "function") window.__setScreen("audit");
          }} style={{
            padding: "10px 20px", background: COLORS.accent, color: COLORS.bg,
            border: "none", borderRadius: 6, cursor: "pointer", fontFamily: "inherit",
            fontSize: 13, fontWeight: 700,
          }}>View Audit Trail →</button>
        </div>
      </div>
    );
  }

  // ── Path A: Auto-execute ─────────────────────────────────────────────────────
  if (path === "A") return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20 }}>
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
            <div style={{ fontSize: 64, fontWeight: 800, color: COLORS.accent,
              animation: "auto-execute 1s ease infinite", fontFamily: "JetBrains Mono, monospace" }}>
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
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20 }}>
      <PathHeader path="B" risk={risk} conf={conf} riskColor={riskColor} />
      <Card style={{ padding: 28, width: "100%", maxWidth: 520 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.text, marginBottom: 8 }}>
          Recommended Fix
        </div>
        <div style={{ fontSize: 13, color: COLORS.textDim, marginBottom: 20, lineHeight: 1.7 }}>
          {rca?.recommended_fix}
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
          Rejection Reason (required to reject)
        </div>
        <textarea
          id="b-reject-reason"
          placeholder="Explain why this fix should not be applied..."
          style={{
            width: "100%", minHeight: 70, padding: "10px 12px", marginBottom: 16,
            background: COLORS.surface, border: `1px solid ${COLORS.border}`,
            borderRadius: 6, color: COLORS.text, fontSize: 12,
            fontFamily: "inherit", resize: "vertical", outline: "none",
          }}
        />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <button onClick={() => executeAction("approve")} disabled={loading} style={{
            padding: "14px", background: COLORS.p3, color: COLORS.bg,
            border: "none", borderRadius: 8, fontSize: 14, fontWeight: 800,
            cursor: loading ? "not-allowed" : "pointer", fontFamily: "inherit",
          }}>
            {loading ? <Spinner /> : "✓ APPROVE & EXECUTE"}
          </button>
          <button onClick={() => setRejectModal(true)} disabled={loading} style={{
            padding: "14px", background: COLORS.p1 + "22", color: COLORS.p1,
            border: `1px solid ${COLORS.p1}44`, borderRadius: 8, fontSize: 14,
            fontWeight: 800, cursor: loading ? "not-allowed" : "pointer", fontFamily: "inherit",
          }}>✕ REJECT FIX</button>
        </div>
        {rejectModal && (
          <RejectModal ticket={ticket} path={path} rca={rca}
            onClose={() => setRejectModal(false)}
            onRejected={() => { setRejectModal(false); onCompleteFired.current = false; setResult({ action:"reject", outcome:"rejected", ticket_id:ticket.id, timestamp:new Date().toISOString() }); }}
          />)}
      </Card>
    </div>
  );

  // ── Path C: Mandatory review ──────────────────────────────────────────────────
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", padding: 40, gap: 20 }}>
      <PathHeader path="C" risk={risk} conf={conf} riskColor={riskColor} />
      <Card style={{ padding: 28, width: "100%", maxWidth: 560 }}>
        <div style={{ padding: "12px 16px", background: COLORS.p1 + "15",
          borderRadius: 6, borderLeft: `3px solid ${COLORS.p1}`,
          fontSize: 12, color: COLORS.p1, marginBottom: 20 }}>
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
          {loading ? <span style={{ display:"flex",alignItems:"center",justifyContent:"center",gap:8 }}>
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
      <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: "0.08em",
        color: riskColor, marginBottom: 4 }}>{title}</div>
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
  const [filter, setFilter]   = useState("ALL");

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

  const EVENT_TYPES = ["ALL","INGEST","PREDICT","RCA","APPROVE","EXECUTE","ROLLBACK","REJECT","RESOLVE","AUTO_APPROVE","RERAISE"];
  const visible = filter === "ALL" ? events : events.filter(e => e.event_type === filter);

  function exportCSV() {
    if (!visible.length) return;
    const headers = Object.keys(visible[0]).join(",");
    const rows = visible.map(e =>
      Object.values(e).map(v => `"${String(v||"").replace(/"/g,'""')}"`).join(",")
    );
    const csv  = [headers, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = `opsai_audit_${Date.now()}.csv`;
    a.click(); URL.revokeObjectURL(url);
  }

  const EVENT_COLOR = {
    INGEST: COLORS.textDim, PREDICT: COLORS.accent, RCA: "#A78BFA",
    APPROVE: COLORS.p3, EXECUTE: COLORS.p3, REJECT: "#CC2244",
    ROLLBACK: "#FF6B35", RESOLVE: COLORS.p3, OVERRIDE: COLORS.p1,
    AUTO_APPROVE: COLORS.accent, RERAISE: "#7B61FF",
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
              {["TIMESTAMP","EVENT","TICKET","OPERATOR","PATH","CONFIDENCE","RISK","ACTION","OUTCOME"].map(h => (
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
                    {e.timestamp?.slice(0,16) || "–"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <Badge label={e.event_type} color={eColor} />
                  </td>
                  <td className="mono" style={{ padding: "8px 10px", color: COLORS.accent, fontSize: 10 }}>
                    {e.ticket_id?.slice(0,14) || "–"}
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
                  <td style={{ padding: "8px 10px", maxWidth: 200, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap", color: COLORS.text, fontSize: 11 }}>
                    {e.action_taken || e.reasoning?.slice(0,60) || "–"}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {e.outcome && (
                      <Badge label={e.outcome}
                        color={e.outcome === "success" || e.outcome === "resolved" || e.outcome === "created"
                          ? COLORS.p3
                          : e.outcome === "rolled_back" ? COLORS.p2
                          : e.outcome === "rejected"    ? COLORS.p1
                          : COLORS.textDim} />
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

// ── Screen 5: Memory Browser — all 46,000 tickets ────────────────────────────
function MemoryBrowser() {
  const [overview, setOverview]   = useState(null);
  const [tickets,  setTickets]    = useState([]);
  const [total,    setTotal]      = useState(0);
  const [loading,  setLoading]    = useState(false);
  const [search,   setSearch]     = useState("");
  const [sevFilter,setSevFilter]  = useState("");
  const [catFilter,setCatFilter]  = useState("");
  const [statFilter,setStatFilter]= useState("");
  const [offset,   setOffset]     = useState(0);
  const [solutionTicket, setSolutionTicket] = useState(null);
  const PAGE = 100;

  // Load overview stats; refresh alongside ticket list
  useEffect(() => {
    apiFetch("/tickets/overview").then(d => { if (d) setOverview(d); });
  }, []);

  // Refresh overview when ticket list is refreshed
  function refreshAll() {
    apiFetch("/tickets/overview").then(d => { if (d) setOverview(d); });
    doSearch(0);
  }

  // Search tickets when filters change
  const doSearch = useCallback(async (off = 0) => {
    setLoading(true);
    const params = new URLSearchParams({
      limit: PAGE, offset: off,
      ...(search    && { q: search }),
      ...(sevFilter && { severity: sevFilter }),
      ...(catFilter && { category: catFilter }),
      ...(statFilter&& { status:   statFilter }),
    });
    const data = await apiFetch(`/tickets/search?${params}`);
    if (data) {
      setTickets(data.tickets || []);
      setTotal(data.total || 0);
      setOffset(off);
    }
    setLoading(false);
  }, [search, sevFilter, catFilter, statFilter]);

  useEffect(() => { doSearch(0); }, [doSearch]);

  const categories = ["","Database","Network","Authentication","Infrastructure","Application","General","Cache","Maintenance","UI","Performance"];
  const statuses   = ["","open","pending_approval","resolved","rolled_back","rejected"];

  function exportCSV() {
    if (!tickets.length) return;
    const headers = Object.keys(tickets[0]).join(",");
    const rows    = tickets.map(t =>
      Object.values(t).map(v => `"${String(v||"").replace(/"/g,'""')}"`).join(",")
    );
    const blob = new Blob([[headers,...rows].join("\n")], { type:"text/csv" });
    const a    = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `opsai_memory_${Date.now()}.csv`;
    a.click();
  }

  return (
    <div style={{ height:"100%", display:"flex", flexDirection:"column", padding:24, gap:16, overflow:"hidden" }}>

      {/* Header */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start" }}>
        <div>
          <div style={{ fontSize:18, fontWeight:800, letterSpacing:"-0.02em" }}>
            Memory Browser
          </div>
          <div style={{ fontSize:12, color:COLORS.textDim, marginTop:2 }}>
            Full dataset — {total.toLocaleString()} tickets · This is the AI's long-term memory
          </div>
        </div>
        <div style={{ display:"flex", gap:8 }}>
          <button onClick={refreshAll} style={{
            padding:"8px 16px", background:COLORS.surface, color:COLORS.textDim,
            border:`1px solid ${COLORS.border}`, borderRadius:6, fontSize:12, fontWeight:700,
            cursor:"pointer", fontFamily:"inherit",
          }}>↺ REFRESH</button>
          <button onClick={exportCSV} style={{
            padding:"8px 16px", background:COLORS.accent, color:COLORS.bg,
            border:"none", borderRadius:6, fontSize:12, fontWeight:700,
            cursor:"pointer", fontFamily:"inherit",
          }}>⬇ EXPORT CSV</button>
        </div>
      </div>

      {/* Overview stat cards */}
      {overview && (
        <div style={{ display:"grid", gridTemplateColumns:"repeat(5,1fr)", gap:10 }}>
          <Card style={{ padding:"12px 14px" }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em" }}>TOTAL TICKETS</div>
            <div style={{ fontSize:24, fontWeight:800, color:COLORS.accent, lineHeight:1.2, marginTop:4 }}>
              {(overview.by_severity?.reduce((a,r)=>a+r.count,0)||0).toLocaleString()}
            </div>
          </Card>
          <Card style={{ padding:"12px 14px" }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em" }}>WITH RESOLUTION</div>
            <div style={{ fontSize:24, fontWeight:800, color:COLORS.p3, lineHeight:1.2, marginTop:4 }}>
              {(overview.with_resolution||0).toLocaleString()}
            </div>
          </Card>
          <Card style={{ padding:"12px 14px" }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em" }}>AVG MTTR</div>
            <div style={{ fontSize:24, fontWeight:800, color:COLORS.p2, lineHeight:1.2, marginTop:4 }}>
              {overview.avg_mttr_hrs ? `${overview.avg_mttr_hrs}h` : "—"}
            </div>
          </Card>
          {(overview.by_severity||[]).filter(r=>["P1","P2"].includes(r.severity)).map(r=>(
            <Card key={r.severity} style={{ padding:"12px 14px" }}>
              <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em" }}>
                {r.severity} TICKETS
              </div>
              <div style={{ fontSize:24, fontWeight:800, color:SEV_COLOR[r.severity], lineHeight:1.2, marginTop:4 }}>
                {r.count.toLocaleString()}
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Category breakdown bar */}
      {overview?.by_category && (
        <Card style={{ padding:"14px 16px" }}>
          <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em", marginBottom:10 }}>
            DISTRIBUTION BY CATEGORY
          </div>
          <div style={{ display:"flex", gap:6, flexWrap:"wrap" }}>
            {overview.by_category.map(r => {
              const total_all = overview.by_category.reduce((a,x)=>a+x.count,0);
              const pct = Math.round((r.count/total_all)*100);
              return (
                <div key={r.category} onClick={() => setCatFilter(catFilter===r.category?"":r.category)}
                  style={{
                    padding:"6px 12px", borderRadius:4, cursor:"pointer",
                    background: catFilter===r.category ? COLORS.accent+"33" : COLORS.surface,
                    border:`1px solid ${catFilter===r.category ? COLORS.accent : COLORS.border}`,
                    display:"flex", alignItems:"center", gap:6,
                  }}>
                  <span style={{ fontSize:11, fontWeight:700, color:catFilter===r.category?COLORS.accent:COLORS.text }}>
                    {r.category}
                  </span>
                  <span className="mono" style={{ fontSize:10, color:COLORS.textDim }}>
                    {r.count.toLocaleString()} ({pct}%)
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Search + filters */}
      <div style={{ display:"flex", gap:10, alignItems:"center" }}>
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search descriptions, resolutions, ticket IDs..."
          style={{
            flex:1, padding:"9px 14px",
            background:COLORS.surface, border:`1px solid ${COLORS.border}`,
            borderRadius:6, color:COLORS.text, fontSize:13,
            fontFamily:"inherit", outline:"none",
          }}
        />
        {[
          { val:sevFilter,  set:setSevFilter,  opts:["","P1","P2","P3"],  label:"Severity" },
          { val:statFilter, set:setStatFilter, opts:statuses,              label:"Status"   },
        ].map(f => (
          <select key={f.label} value={f.val} onChange={e=>f.set(e.target.value)} style={{
            padding:"9px 12px", background:COLORS.surface, border:`1px solid ${COLORS.border}`,
            borderRadius:6, color:COLORS.text, fontSize:12, fontFamily:"inherit",
            outline:"none", cursor:"pointer", minWidth:110,
          }}>
            <option value="">{f.label}: All</option>
            {f.opts.filter(o=>o).map(o=><option key={o} value={o}>{o}</option>)}
          </select>
        ))}
        {(search||sevFilter||catFilter||statFilter) && (
          <button onClick={()=>{setSearch("");setSevFilter("");setCatFilter("");setStatFilter("");}}
            style={{
              padding:"9px 14px", background:COLORS.border, color:COLORS.textDim,
              border:"none", borderRadius:6, fontSize:12, cursor:"pointer", fontFamily:"inherit",
            }}>
            ✕ Clear
          </button>
        )}
      </div>

      {/* Results count */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
        <div className="mono" style={{ fontSize:10, color:COLORS.textDim }}>
          {loading ? "Searching..." : `${total.toLocaleString()} results · showing ${offset+1}–${Math.min(offset+PAGE, total)}`}
        </div>
        <div style={{ display:"flex", gap:8 }}>
          <button onClick={()=>doSearch(Math.max(0,offset-PAGE))} disabled={offset===0} style={{
            padding:"4px 12px", background:COLORS.surface, color:offset===0?COLORS.border:COLORS.text,
            border:`1px solid ${COLORS.border}`, borderRadius:4, fontSize:11,
            cursor:offset===0?"not-allowed":"pointer", fontFamily:"inherit",
          }}>← Prev</button>
          <button onClick={()=>doSearch(offset+PAGE)} disabled={offset+PAGE>=total} style={{
            padding:"4px 12px", background:COLORS.surface,
            color:offset+PAGE>=total?COLORS.border:COLORS.text,
            border:`1px solid ${COLORS.border}`, borderRadius:4, fontSize:11,
            cursor:offset+PAGE>=total?"not-allowed":"pointer", fontFamily:"inherit",
          }}>Next →</button>
        </div>
      </div>

      {/* Ticket table */}
      <div style={{ flex:1, overflowY:"auto" }}>
        {loading && <div style={{ textAlign:"center", padding:30 }}><Spinner /></div>}
        {!loading && tickets.length === 0 && (
          <div style={{ textAlign:"center", padding:40, color:COLORS.textDim, fontSize:13 }}>
            No tickets match your search
          </div>
        )}
        <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
          <thead>
            <tr style={{ position:"sticky", top:0, background:COLORS.surface }}>
              {["TICKET ID","SEVERITY","CATEGORY","DESCRIPTION","RESOLUTION","MTTR","STATUS","SOLUTION"].map(h=>(
                <th key={h} className="mono" style={{
                  padding:"8px 10px", textAlign:"left", fontSize:9,
                  color:COLORS.textDim, letterSpacing:"0.08em",
                  borderBottom:`1px solid ${COLORS.border}`, whiteSpace:"nowrap",
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tickets.map((t,i) => {
              const hasResolution = t.resolution_notes &&
                !["","nan","None","NaN"].includes(t.resolution_notes);
              return (
                <tr key={t.id||i} style={{ borderBottom:`1px solid ${COLORS.border}22` }}
                  onMouseEnter={e=>e.currentTarget.style.background=COLORS.border+"33"}
                  onMouseLeave={e=>e.currentTarget.style.background="transparent"}>
                  <td className="mono" style={{ padding:"8px 10px", color:COLORS.accent, fontSize:10, whiteSpace:"nowrap" }}>
                    {t.id}
                  </td>
                  <td style={{ padding:"8px 10px", whiteSpace:"nowrap" }}>
                    <Badge label={t.severity||"?"} color={SEV_COLOR[t.severity]||COLORS.textDim} dim={SEV_DIM[t.severity]} />
                  </td>
                  <td style={{ padding:"8px 10px", whiteSpace:"nowrap" }}>
                    <Badge label={t.category||"General"} color={COLORS.textDim} />
                  </td>
                  <td style={{ padding:"8px 10px", maxWidth:260,
                    overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap",
                    color:COLORS.text }}>
                    {t.description||"—"}
                  </td>
                  <td style={{ padding:"8px 10px", maxWidth:220,
                    overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap",
                    color: hasResolution ? COLORS.p3 : COLORS.border,
                    fontStyle: hasResolution ? "normal" : "italic", fontSize:11 }}>
                    {hasResolution ? t.resolution_notes : "No resolution recorded"}
                  </td>
                  <td className="mono" style={{ padding:"8px 10px", color:COLORS.textDim, fontSize:10, whiteSpace:"nowrap" }}>
                    {t.resolution_time_hrs ? `${t.resolution_time_hrs}h` : "—"}
                  </td>
                  <td style={{ padding:"8px 10px", whiteSpace:"nowrap" }}>
                    <Badge label={STATUS_LABEL[t.status] || t.status?.replace("_"," ").toUpperCase() || "?"} color={
                      STATUS_COLOR[t.status] || COLORS.textDim
                    } />
                  </td>
                  <td style={{ padding:"8px 10px", whiteSpace:"nowrap" }}>
                    {hasResolution ? (
                      <button onClick={() => setSolutionTicket(t)} style={{
                        padding:"3px 10px", background:COLORS.p3+"22", color:COLORS.p3,
                        border:`1px solid ${COLORS.p3}44`, borderRadius:4,
                        fontSize:10, fontWeight:700, cursor:"pointer", fontFamily:"inherit",
                      }}>View</button>
                    ) : (
                      <span style={{ fontSize:10, color:COLORS.border }}>—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Solution floating modal */}
      {solutionTicket && <SolutionModal ticket={solutionTicket} onClose={() => setSolutionTicket(null)} />}
    </div>
  );
}

// ── SolutionModal — detailed RCA view from Memory Browser ─────────────────────
function SolutionModal({ ticket, onClose }) {
  const [rca, setRca] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiFetch(`/tickets/${ticket.id}/rca/result`).then(data => {
      if (data && data.status !== "pending") setRca(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [ticket.id]);

  // Parse resolution_notes sections (from _build_detailed_resolution)
  const notes = ticket.resolution_notes || "";
  const sections = {};
  for (const line of notes.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("ROOT CAUSE:")) sections.rootCause = trimmed.replace("ROOT CAUSE:", "").trim();
    else if (trimmed.startsWith("RECOMMENDED FIX:")) sections.recFix = trimmed.replace("RECOMMENDED FIX:", "").trim();
    else if (trimmed.startsWith("PATTERN MATCH:")) sections.pattern = trimmed.replace("PATTERN MATCH:", "").trim();
    else if (trimmed.startsWith("WARNINGS:")) sections.warnings = trimmed.replace("WARNINGS:", "").trim();
    else if (trimmed.startsWith("EXECUTION RESULT:")) sections.execResult = trimmed.replace("EXECUTION RESULT:", "").trim();
    else if (trimmed.startsWith("FIX TYPE:")) sections.fixType = trimmed.replace("FIX TYPE:", "").trim();
  }
  // Extract numbered fix steps
  const stepLines = notes.split("\n").filter(l => /^\s+\d+\./.test(l)).map(l => l.replace(/^\s+\d+\.\s*/, ""));

  // Prefer RCA data over parsed notes
  const rootCause = rca?.root_cause || sections.rootCause || "";
  const recFix = rca?.recommended_fix || sections.recFix || "";
  const pattern = rca?.pattern_match || sections.pattern || "";
  const warnings = rca?.warnings || sections.warnings || "";
  const fixSteps = (rca?.fix_steps && Array.isArray(rca.fix_steps) && rca.fix_steps.length > 0)
    ? rca.fix_steps
    : (stepLines.length > 0 ? stepLines : []);

  const statusColor = STATUS_COLOR[ticket.status] || COLORS.textDim;
  const statusLabel = STATUS_LABEL[ticket.status] || ticket.status?.replace("_"," ").toUpperCase() || "?";

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }} style={{
      position:"fixed", inset:0, background:"rgba(0,0,0,0.8)",
      zIndex:300, display:"flex", alignItems:"center", justifyContent:"center",
      animation:"fade-in 0.2s ease",
    }}>
      <div onClick={e=>e.stopPropagation()} style={{
        width:600, maxHeight:"85vh", background:COLORS.card,
        border:`1px solid ${COLORS.border}`, borderRadius:12,
        padding:28, animation:"slide-in 0.25s ease", overflowY:"auto",
      }}>
        {/* Header */}
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:16 }}>
          <div>
            <div style={{ fontSize:16, fontWeight:800 }}>Detailed Solution</div>
            <div className="mono" style={{ fontSize:10, color:COLORS.accent, marginTop:2 }}>
              {ticket.id}
            </div>
          </div>
          <button onClick={onClose} style={{
            background:"none", border:"none", color:COLORS.textDim,
            cursor:"pointer", fontSize:16, padding:"2px 6px",
          }}>✕</button>
        </div>

        {/* Badges */}
        <div style={{ display:"flex", gap:8, flexWrap:"wrap", marginBottom:14 }}>
          <Badge label={ticket.severity||"?"} color={SEV_COLOR[ticket.severity]||COLORS.textDim} dim={SEV_DIM[ticket.severity]} />
          <Badge label={ticket.category||"General"} color={COLORS.textDim} />
          <Badge label={statusLabel} color={statusColor} />
          {sections.fixType && <Badge label={sections.fixType.replace("_"," ")} color={COLORS.accent} />}
        </div>

        {/* Description */}
        <div style={{ fontSize:12, color:COLORS.textDim, marginBottom:16, lineHeight:1.6, padding:"10px 14px",
          background:COLORS.surface, borderRadius:6, border:`1px solid ${COLORS.border}` }}>
          {ticket.description}
        </div>

        {loading && <div style={{ textAlign:"center", padding:20 }}><Spinner /></div>}

        {/* Root Cause */}
        {rootCause && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.p1, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              ROOT CAUSE
            </div>
            <div style={{ padding:"12px 14px", background:COLORS.p1+"10", border:`1px solid ${COLORS.p1}33`,
              borderRadius:6, fontSize:13, color:COLORS.text, lineHeight:1.7 }}>
              {rootCause}
            </div>
          </div>
        )}

        {/* Recommended Fix */}
        {recFix && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.p3, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              RECOMMENDED FIX
            </div>
            <div style={{ padding:"12px 14px", background:COLORS.p3+"10", border:`1px solid ${COLORS.p3}33`,
              borderRadius:6, fontSize:13, color:COLORS.text, lineHeight:1.7 }}>
              {recFix}
            </div>
          </div>
        )}

        {/* Fix Steps */}
        {fixSteps.length > 0 && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.accent, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              FIX STEPS
            </div>
            <div style={{ padding:"12px 14px", background:COLORS.accent+"08", border:`1px solid ${COLORS.accent}22`,
              borderRadius:6 }}>
              {fixSteps.map((step, i) => (
                <div key={i} style={{ display:"flex", gap:10, marginBottom:i < fixSteps.length-1 ? 8 : 0,
                  fontSize:12, color:COLORS.text, lineHeight:1.6 }}>
                  <span className="mono" style={{ color:COLORS.accent, fontWeight:800, flexShrink:0 }}>{i+1}.</span>
                  <span>{step}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Pattern Match */}
        {pattern && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:"#A78BFA", letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              PATTERN MATCH
            </div>
            <div style={{ padding:"10px 14px", background:"#A78BFA10", border:"1px solid #A78BFA33",
              borderRadius:6, fontSize:12, color:COLORS.text, lineHeight:1.6 }}>
              {pattern}
            </div>
          </div>
        )}

        {/* Warnings */}
        {warnings && warnings.toLowerCase() !== "null" && warnings.toLowerCase() !== "none" && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.p2, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              ⚠ WARNINGS
            </div>
            <div style={{ padding:"10px 14px", background:COLORS.p2+"10", border:`1px solid ${COLORS.p2}33`,
              borderRadius:6, fontSize:12, color:COLORS.p2, lineHeight:1.6 }}>
              {warnings}
            </div>
          </div>
        )}

        {/* Execution result */}
        {sections.execResult && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.p3, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              EXECUTION RESULT
            </div>
            <div className="mono" style={{ padding:"10px 14px", background:COLORS.p3+"10", border:`1px solid ${COLORS.p3}33`,
              borderRadius:6, fontSize:11, color:COLORS.p3 }}>
              {sections.execResult}
            </div>
          </div>
        )}

        {/* Fallback: raw resolution_notes if none of the above rendered */}
        {!rootCause && !recFix && fixSteps.length === 0 && notes && (
          <div style={{ marginBottom:14 }}>
            <div className="mono" style={{ fontSize:9, color:COLORS.textDim, letterSpacing:"0.1em", marginBottom:6, fontWeight:700 }}>
              RESOLUTION NOTES
            </div>
            <div style={{ padding:"12px 14px", background:COLORS.p3+"10", border:`1px solid ${COLORS.p3}33`,
              borderRadius:6, fontSize:13, color:COLORS.p3, lineHeight:1.7, whiteSpace:"pre-wrap" }}>
              {notes}
            </div>
          </div>
        )}

        {/* Meta */}
        {ticket.resolution_time_hrs && (
          <div className="mono" style={{ fontSize:10, color:COLORS.textDim, marginTop:6 }}>
            Resolution time: {ticket.resolution_time_hrs}h
          </div>
        )}
        {rca?.confidence_score != null && (
          <div style={{ display:"flex", gap:12, marginTop:8 }}>
            <div className="mono" style={{ fontSize:10, color:COLORS.textDim }}>
              Confidence: {rca.confidence_score}%
            </div>
            {rca.risk_tier && (
              <Badge label={rca.risk_tier} color={RISK_COLOR[rca.risk_tier] || COLORS.textDim} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── RejectModal — used by Path B and Path C approval ──────────────────────────
function RejectModal({ ticket, path, rca, onClose, onRejected }) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleReject() {
    if (reason.length < 3) return;
    setLoading(true);
    const res = await apiFetch(`/tickets/${ticket.id}/reject_v2`, {
      method: "POST",
      body: JSON.stringify({
        operator_id: "ops_dashboard",
        reject_reason: reason,
        approval_path: path,
        rca_id: rca?.id || null,
      }),
    });
    setLoading(false);
    if (res && !res._error && (res.status === "rejected" || res.ticket_status === "rejected")) {
      onRejected();
    } else {
      onClose();
    }
  }

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)",
      zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center",
      animation: "fade-in 0.2s ease",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 480, background: COLORS.card, border: `1px solid ${COLORS.border}`,
        borderRadius: 12, padding: 28, animation: "slide-in 0.25s ease",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <span style={{ fontSize: 24 }}>⚠️</span>
          <div style={{ fontSize: 16, fontWeight: 800, color: "#CC2244" }}>Reject Recommended Fix</div>
        </div>
        <div style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 14, padding: "8px 12px",
          background: COLORS.surface, borderRadius: 6 }}>
          {ticket.description?.slice(0, 100)}...
        </div>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Rejection Reason *</div>
        <textarea value={reason} onChange={e => setReason(e.target.value)}
          placeholder="Explain why this fix should not be applied..."
          style={{
            width: "100%", minHeight: 80, padding: "10px 12px", marginBottom: 14,
            background: COLORS.surface, border: `1px solid ${COLORS.border}`,
            borderRadius: 6, color: COLORS.text, fontSize: 12,
            fontFamily: "inherit", resize: "vertical", outline: "none",
          }} />
        <div style={{ fontSize: 11, color: COLORS.textDim, marginBottom: 14, padding: "10px 12px",
          background: "#CC224410", border: "1px solid #CC224433", borderRadius: 6 }}>
          <div style={{ fontWeight: 700, marginBottom: 4, color: "#CC2244" }}>Consequences:</div>
          <ul style={{ margin: 0, paddingLeft: 16, lineHeight: 1.8 }}>
            <li>Ticket status → <b>rejected</b></li>
            <li>AI confidence penalised −5pts for this fix type</li>
            <li>Re-raise to engineer becomes available</li>
            <li>Full audit trail entry created</li>
          </ul>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={handleReject} disabled={reason.length < 3 || loading} style={{
            flex: 1, padding: "12px", background: reason.length >= 3 ? "#CC2244" : COLORS.border,
            color: reason.length >= 3 ? "#fff" : COLORS.textDim,
            border: "none", borderRadius: 6, fontSize: 13, fontWeight: 800,
            cursor: reason.length >= 3 && !loading ? "pointer" : "not-allowed", fontFamily: "inherit",
          }}>{loading ? <Spinner /> : "CONFIRM REJECT"}</button>
          <button onClick={onClose} style={{
            padding: "12px 18px", background: COLORS.surface, color: COLORS.textDim,
            border: `1px solid ${COLORS.border}`, borderRadius: 6,
            fontSize: 13, cursor: "pointer", fontFamily: "inherit",
          }}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── ExecutionHistory — render inside RCADetail ────────────────────────────────
function ExecutionHistory({ executions, ticketId, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState(null);

  if (!executions.length) return null;

  const OUTCOME_COLOR = { success: COLORS.p3, failed: COLORS.p1, rolled_back: "#FF6B35" };

  return (
    <Card style={{ padding: 20, marginTop: 12 }}>
      <div onClick={() => setExpanded(!expanded)} style={{
        display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer",
      }}>
        <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", fontWeight: 700 }}>
          EXECUTION HISTORY ({executions.length})
        </div>
        <span style={{ fontSize: 12, color: COLORS.textDim }}>{expanded ? "▲" : "▼"}</span>
      </div>
      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
          {executions.map(exe => {
            let preState, postState;
            try { preState = typeof exe.pre_state === "string" ? JSON.parse(exe.pre_state) : exe.pre_state || {}; } catch { preState = {}; }
            try { postState = typeof exe.post_state === "string" ? JSON.parse(exe.post_state) : exe.post_state || {}; } catch { postState = {}; }
            return (
              <div key={exe.id} style={{
                padding: "12px 14px", background: COLORS.surface,
                borderRadius: 6, border: `1px solid ${COLORS.border}`,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span className="mono" style={{ fontSize: 10, color: COLORS.accent }}>{exe.id?.slice(0, 12)}</span>
                    <Badge label={exe.fix_type || "unknown"} color={COLORS.accent} />
                    <Badge label={exe.outcome || "unknown"} color={OUTCOME_COLOR[exe.outcome] || COLORS.textDim} />
                  </div>
                  {exe.outcome === "success" && !exe.rolled_back && (
                    <button onClick={() => setRollbackTarget(exe)} style={{
                      padding: "4px 12px", background: "#FF6B3522", color: "#FF6B35",
                      border: "1px solid #FF6B3544", borderRadius: 4,
                      fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
                    }}>↩ Rollback</button>
                  )}
                </div>
                <div className="mono" style={{ fontSize: 10, color: COLORS.textDim }}>
                  {exe.executed_at?.slice(0, 19) || "–"}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {rollbackTarget && (
        <RollbackModal execution={rollbackTarget}
          onClose={() => setRollbackTarget(null)}
          onRolledBack={() => { setRollbackTarget(null); if (onRefresh) onRefresh(); }}
        />)}
    </Card>
  );
}

// ── RollbackModal ─────────────────────────────────────────────────────────────
function RollbackModal({ execution, onClose, onRolledBack }) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);

  let preState, postState;
  try { preState = typeof execution.pre_state === "string" ? JSON.parse(execution.pre_state) : execution.pre_state || {}; } catch { preState = {}; }
  try { postState = typeof execution.post_state === "string" ? JSON.parse(execution.post_state) : execution.post_state || {}; } catch { postState = {}; }

  async function handleRollback() {
    if (reason.length < 3) return;
    setLoading(true);
    const res = await apiFetch(`/executions/${execution.id}/rollback`, {
      method: "POST",
      body: JSON.stringify({ operator_id: "ops_dashboard", rollback_reason: reason }),
    });
    setLoading(false);
    if (res?.status === "rolled_back") onRolledBack();
    else onClose();
  }

  const allKeys = [...new Set([...Object.keys(preState), ...Object.keys(postState)])];

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.8)",
      zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center",
      animation: "fade-in 0.2s ease",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 540, maxHeight: "85vh", overflowY: "auto",
        background: COLORS.card, border: `1px solid ${COLORS.border}`,
        borderRadius: 12, padding: 28, animation: "slide-in 0.25s ease",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <span style={{ fontSize: 24 }}>↩️</span>
          <div style={{ fontSize: 16, fontWeight: 800, color: "#FF6B35" }}>Rollback Execution</div>
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          <Badge label={execution.id?.slice(0, 12)} color={COLORS.accent} />
          <Badge label={execution.fix_type} color={COLORS.accent} />
        </div>

        {/* Pre vs Post state diff */}
        <div style={{ fontSize: 10, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 8, fontWeight: 700 }}>STATE DIFF</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1, marginBottom: 14,
          background: COLORS.border, borderRadius: 6, overflow: "hidden", fontSize: 11 }}>
          <div style={{ background: COLORS.surface, padding: "8px 10px", fontWeight: 700, color: COLORS.textDim }}>BEFORE</div>
          <div style={{ background: COLORS.surface, padding: "8px 10px", fontWeight: 700, color: COLORS.textDim }}>AFTER</div>
          {allKeys.map(k => (
            <>{/* Fragment has key on parent div */}
              <div key={`pre-${k}`} className="mono" style={{ background: COLORS.card, padding: "4px 10px", color: COLORS.text }}>
                {k}: {JSON.stringify(preState[k] ?? "–")}
              </div>
              <div key={`post-${k}`} className="mono" style={{
                background: COLORS.card, padding: "4px 10px",
                color: JSON.stringify(preState[k]) !== JSON.stringify(postState[k]) ? COLORS.p2 : COLORS.text,
              }}>
                {k}: {JSON.stringify(postState[k] ?? "–")}
              </div>
            </>
          ))}
        </div>

        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Rollback Reason *</div>
        <textarea value={reason} onChange={e => setReason(e.target.value)}
          placeholder="Why is this rollback necessary?"
          style={{
            width: "100%", minHeight: 70, padding: "10px 12px", marginBottom: 14,
            background: COLORS.surface, border: `1px solid ${COLORS.border}`,
            borderRadius: 6, color: COLORS.text, fontSize: 12,
            fontFamily: "inherit", resize: "vertical", outline: "none",
          }} />
        <div style={{ fontSize: 11, color: COLORS.textDim, marginBottom: 14, padding: "10px 12px",
          background: "#FF6B3510", border: "1px solid #FF6B3533", borderRadius: 6 }}>
          <div style={{ fontWeight: 700, marginBottom: 4, color: "#FF6B35" }}>Consequences:</div>
          <ul style={{ margin: 0, paddingLeft: 16, lineHeight: 1.8 }}>
            <li>Pre-state will be restored</li>
            <li>AI confidence penalised −10pts for this fix type</li>
            <li>Ticket status → <b>rolled_back</b></li>
            <li>Re-raise to engineer enabled</li>
            <li>Full audit trail entry created</li>
          </ul>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={handleRollback} disabled={reason.length < 3 || loading} style={{
            flex: 1, padding: "12px", background: reason.length >= 3 ? "#FF6B35" : COLORS.border,
            color: reason.length >= 3 ? "#fff" : COLORS.textDim,
            border: "none", borderRadius: 6, fontSize: 13, fontWeight: 800,
            cursor: reason.length >= 3 && !loading ? "pointer" : "not-allowed", fontFamily: "inherit",
          }}>{loading ? <Spinner /> : "CONFIRM ROLLBACK"}</button>
          <button onClick={onClose} style={{
            padding: "12px 18px", background: COLORS.surface, color: COLORS.textDim,
            border: `1px solid ${COLORS.border}`, borderRadius: 6,
            fontSize: 13, cursor: "pointer", fontFamily: "inherit",
          }}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── ReraisePanel — for rolled_back/rejected tickets ───────────────────────────
function ReraisePanel({ ticket }) {
  const [engineer, setEngineer] = useState("L2_Support_Queue");
  const [reason, setReason] = useState("Automated fix failed — requires expert review");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const QUEUES = ["L2_Support_Queue", "L3_Infrastructure", "Database_Team", "Network_Ops", "Security_Ops"];

  async function handleReraise() {
    if (reason.length < 3) return;
    setLoading(true);
    const res = await apiFetch(`/tickets/${ticket.id}/reraise`, {
      method: "POST",
      body: JSON.stringify({
        operator_id: "ops_dashboard",
        reraise_reason: reason,
        additional_context: context,
        assigned_engineer: engineer,
      }),
    });
    setLoading(false);
    if (res?.status === "reraised") {
      setResult(res);
      setTimeout(() => {
        if (typeof window.__setScreen === "function") window.__setScreen("feed");
      }, 3000);
    }
  }

  if (result) {
    return (
      <Card style={{ padding: 24, borderLeft: "3px solid #7B61FF", animation: "slide-in 0.3s ease" }}>
        <div style={{ fontSize: 24, marginBottom: 8 }}>✅</div>
        <div style={{ fontSize: 16, fontWeight: 800, color: "#7B61FF", marginBottom: 12 }}>TICKET ESCALATED</div>
        <div style={{ fontSize: 13, color: COLORS.text, marginBottom: 8 }}>
          A support engineer will pick this up from the <b style={{ color: "#7B61FF" }}>{result.assigned_to}</b> queue.
        </div>
        <div className="mono" style={{ fontSize: 11, color: COLORS.textDim }}>
          {result.ticket_id} · This action has been logged to the audit trail.
        </div>
        <div className="mono" style={{ fontSize: 10, color: COLORS.textDim, marginTop: 8 }}>
          Redirecting to Live Feed in 3s...
        </div>
      </Card>
    );
  }

  return (
    <Card style={{ padding: 20, borderLeft: "3px solid #7B61FF" }}>
      <div style={{ fontSize: 10, color: "#7B61FF", letterSpacing: "0.1em", fontWeight: 700, marginBottom: 10 }}>
        ESCALATE TO SUPPORT ENGINEER
      </div>
      <div style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 14, lineHeight: 1.6 }}>
        This ticket was <b style={{ color: STATUS_COLOR[ticket.status] || COLORS.textDim }}>{STATUS_LABEL[ticket.status] || ticket.status}</b> and
        requires human expert review.
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Engineer Queue</div>
      <select value={engineer} onChange={e => setEngineer(e.target.value)} style={{
        width: "100%", padding: "9px 12px", marginBottom: 14,
        background: COLORS.surface, border: `1px solid ${COLORS.border}`,
        borderRadius: 6, color: COLORS.text, fontSize: 12,
        fontFamily: "inherit", outline: "none", cursor: "pointer",
      }}>
        {QUEUES.map(q => <option key={q} value={q}>{q}</option>)}
      </select>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Re-raise Reason *</div>
      <textarea value={reason} onChange={e => setReason(e.target.value)} style={{
        width: "100%", minHeight: 50, padding: "10px 12px", marginBottom: 14,
        background: COLORS.surface, border: `1px solid ${COLORS.border}`,
        borderRadius: 6, color: COLORS.text, fontSize: 12,
        fontFamily: "inherit", resize: "vertical", outline: "none",
      }} />
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Additional Context (optional)</div>
      <textarea value={context} onChange={e => setContext(e.target.value)}
        placeholder="Describe any new observations, error codes, or context since the last attempt..."
        style={{
          width: "100%", minHeight: 50, padding: "10px 12px", marginBottom: 16,
          background: COLORS.surface, border: `1px solid ${COLORS.border}`,
          borderRadius: 6, color: COLORS.text, fontSize: 12,
          fontFamily: "inherit", resize: "vertical", outline: "none",
        }} />
      <button onClick={handleReraise} disabled={reason.length < 3 || loading} style={{
        width: "100%", padding: "14px", background: reason.length >= 3 ? "#7B61FF" : COLORS.border,
        color: reason.length >= 3 ? "#fff" : COLORS.textDim,
        border: "none", borderRadius: 8, fontSize: 14, fontWeight: 800,
        cursor: reason.length >= 3 && !loading ? "pointer" : "not-allowed", fontFamily: "inherit",
      }}>
        {loading ? <span style={{ display:"flex",alignItems:"center",justifyContent:"center",gap:8 }}><Spinner /> Escalating...</span>
          : "RE-RAISE TO ENGINEER"}
      </button>
    </Card>
  );
}

// ── Ingest Form — floating + button, modal with presets ──────────────────────
function IngestForm({ onIngested }) {
  const [open, setOpen]     = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState(null);
  const [form, setForm] = useState({
    description: "", ci_cat: "storage", urgency: "3",
    impact: "3", alert_status: "False", source: "manual",
  });

  const PRESETS = [
    { label: "🔴 P1 — DB Outage",
      data: { description: "SAN storage array reporting hardware fault, database writes failing across cluster",
              severity: "P1", ci_cat: "storage", urgency: "1", impact: "1", alert_status: "True" } },
    { label: "🔴 P1 — Auth Down",
      data: { description: "Authentication service returning 500 errors, login requests rejected for all tenants",
              severity: "P1", ci_cat: "application", urgency: "1", impact: "1", alert_status: "True" } },
    { label: "🔴 P1 — Network Down",
      data: { description: "Core network switch failure causing complete loss of connectivity across all offices",
              severity: "P1", ci_cat: "network", urgency: "1", impact: "1", alert_status: "True" } },
    { label: "🔴 P1 — Security Breach",
      data: { description: "Unauthorized access detected on production database, potential data exfiltration in progress",
              severity: "P1", ci_cat: "application", urgency: "1", impact: "1", alert_status: "True" } },
    { label: "🟡 P2 — App Slow",
      data: { description: "Web application response times elevated to 8 seconds, subset of users affected",
              severity: "P2", ci_cat: "subapplication", urgency: "2", impact: "2", alert_status: "False" } },
    { label: "🟡 P2 — DB Lag",
      data: { description: "Database replication lag increasing on secondary node, reads becoming stale",
              severity: "P2", ci_cat: "storage", urgency: "2", impact: "3", alert_status: "False" } },
    { label: "🟡 P2 — High CPU",
      data: { description: "Application server CPU usage sustained above 90% for 30 minutes causing degraded performance",
              severity: "P2", ci_cat: "subapplication", urgency: "2", impact: "2", alert_status: "False" } },
    { label: "🟡 P2 — Disk Full",
      data: { description: "Primary log disk on production server at 95% capacity, writes will fail when full",
              severity: "P2", ci_cat: "storage", urgency: "2", impact: "2", alert_status: "True" } },
    { label: "🟡 P2 — VPN Issues",
      data: { description: "Remote VPN service intermittent, affecting remote workforce connectivity",
              severity: "P2", ci_cat: "network", urgency: "2", impact: "3", alert_status: "False" } },
    { label: "🟢 P3 — Certificate",
      data: { description: "SSL certificate on internal reporting dashboard expiring in 14 days, renewal needed",
              severity: "P3", ci_cat: "", urgency: "4", impact: "4", alert_status: "False" } },
    { label: "🟢 P3 — Patch Due",
      data: { description: "Security patch MS-2024-0421 available for critical OS vulnerability, scheduled maintenance required",
              severity: "P3", ci_cat: "", urgency: "3", impact: "3", alert_status: "False" } },
    { label: "🟢 P3 — Account Lock",
      data: { description: "Single user account locked out after failed password attempts, requires manual unlock",
              severity: "P3", ci_cat: "application", urgency: "4", impact: "4", alert_status: "False" } },
  ];

  async function submit() {
    if (!form.description.trim()) return;
    setLoading(true); setResult(null);
    const payload = { ...form };
    // Remove severity key if empty so backend auto-detects
    if (!payload.severity) delete payload.severity;
    const data = await apiFetch("/tickets/ingest", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setLoading(false);
    if (data?.id) {
      setResult(data);
      setForm(f => ({ ...f, description: "", severity: "" }));
      if (onIngested) onIngested(data);
    }
  }

  return (
    <>
      {/* Floating + button */}
      <button onClick={() => setOpen(o => !o)} style={{
        position: "fixed", bottom: 32, right: 32,
        width: 52, height: 52, borderRadius: "50%",
        background: COLORS.accent, color: COLORS.bg,
        border: "none", cursor: "pointer", fontSize: 24, fontWeight: 800,
        boxShadow: `0 0 0 0 ${COLORS.accent}44`,
        animation: "auto-execute 2s ease infinite",
        zIndex: 200, display: "flex", alignItems: "center", justifyContent: "center",
      }} title="Ingest new ticket">
        {open ? "✕" : "+"}
      </button>

      {/* Modal overlay */}
      {open && (
        <div onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)",
            zIndex: 199, display: "flex", alignItems: "center", justifyContent: "center",
            animation: "fade-in 0.2s ease",
          }}>
          <div style={{
            width: 560, background: COLORS.card,
            border: `1px solid ${COLORS.border}`, borderRadius: 12,
            padding: 28, animation: "slide-in 0.25s ease",
          }}>
            <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 4 }}>Ingest New Ticket</div>
            <div style={{ fontSize: 12, color: COLORS.textDim, marginBottom: 18 }}>
              Submits to live API → AI prediction + RCA triggered automatically
            </div>

            {/* Presets */}
            <div className="mono" style={{ fontSize: 9, color: COLORS.textDim, letterSpacing: "0.1em", marginBottom: 8 }}>
              QUICK PRESETS
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 18 }}>
              {PRESETS.map(p => (
                <button key={p.label} onClick={() => setForm(f => ({ ...f, ...p.data }))} style={{
                  padding: "5px 11px", borderRadius: 4, border: `1px solid ${COLORS.border}`,
                  background: COLORS.surface, color: COLORS.text,
                  fontSize: 11, cursor: "pointer", fontFamily: "inherit", fontWeight: 600,
                }}>{p.label}</button>
              ))}
            </div>

            {/* Description */}
            <div className="mono" style={{ fontSize: 9, color: COLORS.textDim, letterSpacing: "0.08em", marginBottom: 6 }}>
              DESCRIPTION *
            </div>
            <textarea value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Describe the incident..."
              rows={3} style={{
                width: "100%", padding: "10px 12px", marginBottom: 14,
                background: COLORS.surface, border: `1px solid ${COLORS.border}`,
                borderRadius: 6, color: COLORS.text, fontSize: 13,
                fontFamily: "inherit", resize: "vertical", outline: "none",
              }} />

            {/* Field row */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 18 }}>
              {[
                { key: "ci_cat",       label: "CI TYPE",   options: ["storage","application","subapplication","network","hardware",""] },
                { key: "urgency",      label: "URGENCY",   options: ["1","2","3","4"] },
                { key: "impact",       label: "IMPACT",    options: ["1","2","3","4"] },
                { key: "alert_status", label: "ALERT",     options: ["False","True"] },
              ].map(field => (
                <div key={field.key}>
                  <div className="mono" style={{ fontSize: 9, color: COLORS.textDim, marginBottom: 4, letterSpacing: "0.08em" }}>
                    {field.label}
                  </div>
                  <select value={form[field.key]}
                    onChange={e => setForm(f => ({ ...f, [field.key]: e.target.value }))}
                    style={{
                      width: "100%", padding: "6px 8px",
                      background: COLORS.surface, border: `1px solid ${COLORS.border}`,
                      borderRadius: 4, color: COLORS.text, fontSize: 12,
                      fontFamily: "inherit", outline: "none", cursor: "pointer",
                    }}>
                    {field.options.map(o => <option key={o} value={o}>{o || "(auto)"}</option>)}
                  </select>
                </div>
              ))}
            </div>

            {/* Result feedback */}
            {result && (
              <div style={{
                padding: "10px 14px", borderRadius: 6, marginBottom: 14,
                background: COLORS.p3 + "15", border: `1px solid ${COLORS.p3}33`,
              }}>
                <div className="mono" style={{ fontSize: 11, color: COLORS.p3 }}>
                  ✓ {result.id} · {result.severity} · {result.category}
                </div>
                {result.anomaly_flags?.length > 0 && (
                  <div className="mono" style={{ fontSize: 10, color: COLORS.p2, marginTop: 3 }}>
                    ⚠ {result.anomaly_flags.join(", ")}
                  </div>
                )}
              </div>
            )}

            {/* Buttons */}
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={submit} disabled={!form.description.trim() || loading} style={{
                flex: 1, padding: "12px",
                background: form.description.trim() ? COLORS.accent : COLORS.border,
                color: form.description.trim() ? COLORS.bg : COLORS.textDim,
                border: "none", borderRadius: 6, fontSize: 13, fontWeight: 800,
                cursor: form.description.trim() && !loading ? "pointer" : "not-allowed",
                fontFamily: "inherit",
              }}>
                {loading
                  ? <span style={{ display:"flex",alignItems:"center",justifyContent:"center",gap:8 }}><Spinner /> Ingesting...</span>
                  : "▶ INGEST TICKET"}
              </button>
              <button onClick={() => setOpen(false)} style={{
                padding: "12px 18px", background: COLORS.surface, color: COLORS.textDim,
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
  const [screen, setScreen]   = useState("feed");
  const [selected, setSelected] = useState(null);
  const [rcaData, setRcaData]   = useState(null);
  const [predData, setPredData] = useState(null);
  const [apiOk, setApiOk]       = useState(null);

  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = GLOBAL_CSS;
    document.head.appendChild(style);
    return () => document.head.removeChild(style);
  }, []);

  useEffect(() => {
    apiFetch("/health").then(d => setApiOk(d?.status === "ok"));
  }, []);

  // Expose setScreen globally so result screen "View Audit Trail" button works
  useEffect(() => {
    window.__setScreen = setScreen;
    return () => { delete window.__setScreen; };
  }, [setScreen]);

  function handleSelectTicket(ticket) {
    setSelected(ticket);
    setScreen("rca");
  }

  function handleApprove(ticket, rca, pred) {
    setSelected(ticket);
    setRcaData(rca);
    setPredData(pred || null);
    setScreen("approval");
  }

  function handleComplete(resultData) {
    // Update selected ticket status to reflect backend changes
    const statusMap = {
      success:     "resolved",
      rejected:    "rejected",
      rolled_back: "rolled_back",
    };
    const newStatus = statusMap[resultData?.outcome];
    if (newStatus) {
      setSelected(prev => prev ? { ...prev, status: newStatus } : prev);
    }
  }

  const NAV = [
    { id: "feed",     label: "01  LIVE FEED",      icon: "⬡" },
    { id: "rca",      label: "02  RCA DETAIL",     icon: "⬡" },
    { id: "approval", label: "03  APPROVAL",       icon: "⬡" },
    { id: "audit",    label: "04  AUDIT TRAIL",    icon: "⬡" },
    { id: "memory",   label: "05  MEMORY",          icon: "⬡" },
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
          <div className="mono" style={{ fontSize: 9, color: COLORS.textDim,
            letterSpacing: "0.12em", padding: "3px 8px", border: `1px solid ${COLORS.border}`,
            borderRadius: 3 }}>
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
      <div style={{ flex: 1, overflow: "hidden", display: "grid",
        gridTemplateColumns: screen === "feed" ? "1fr 1fr" : "1fr",
        gridTemplateRows: "1fr",
      }}>
        {screen === "feed" && (
          <>
            <div style={{ borderRight: `1px solid ${COLORS.border}`, overflow: "hidden" }}>
              <TicketFeed onSelectTicket={handleSelectTicket} selected={selected} />
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
          <ApprovalWorkflow ticket={selected} rca={rcaData} pred={predData}
            onComplete={handleComplete} />
        )}
        {screen === "audit"    && <AuditTrail />}
        {screen === "memory"   && <MemoryBrowser />}
      </div>

      {/* Floating ingest button — always visible */}
      <IngestForm onIngested={() => {}} />

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
    </div>
  );
}
