import { useCallback, useEffect, useState } from "preact/hooks";

// 当前 UTC ISO 周键（YYYY-Www），仅用于给周选择器一个默认值；不参与任何汇总计算。
function currentWeekKey() {
  const now = new Date();
  const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const dayNum = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dayNum + 3);
  const year = d.getUTCFullYear();
  const firstThursday = new Date(Date.UTC(year, 0, 4));
  const week =
    1 +
    Math.round(
      ((d - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7
    );
  return `${year}-W${String(week).padStart(2, "0")}`;
}

function Stat({ label, value, sub, tone }) {
  return (
    <div class="stat">
      <div class="stat-label">{label}</div>
      <div class={`stat-value ${tone || ""}`}>{value}</div>
      {sub != null && <div class="stat-sub">{sub}</div>}
    </div>
  );
}

// part-to-whole 堆叠条：宽度只做后台占比的视觉缩放，合格/超温量仍以后台数字为准。
function RatioMeter({ summary }) {
  const total = summary.total_count;
  if (!total) {
    return <div class="meter-empty">本周暂无办结读数</div>;
  }
  return (
    <div
      class="meter"
      role="img"
      aria-label={`合格 ${summary.pass_percent}%，超温 ${summary.fail_percent}%`}
    >
      <div class="seg pass" style={{ width: `${summary.pass_ratio * 100}%` }} />
      <div class="seg fail" style={{ width: `${summary.fail_ratio * 100}%` }} />
    </div>
  );
}

function SummaryTiles({ summary }) {
  return (
    <div>
      <div class="kpi-row">
        <Stat label="合格量" value={summary.pass_count} tone="pass" />
        <Stat label="超温量" value={summary.fail_count} tone="fail" />
        <Stat label="办结总量" value={summary.total_count} />
        <Stat
          label="合格率"
          value={`${summary.pass_percent}%`}
          sub={`超温率 ${summary.fail_percent}%`}
        />
      </div>
      <RatioMeter summary={summary} />
      <div class="meter-legend">
        <span class="lg">
          <span class="swatch pass" />
          合格 {summary.pass_count}（{summary.pass_percent}%）
        </span>
        <span class="lg">
          <span class="swatch fail" />
          超温 {summary.fail_count}（{summary.fail_percent}%）
        </span>
      </div>
    </div>
  );
}

function CopyModal({ copy, onClose }) {
  if (!copy) return null;
  return (
    <div class="modal-mask" onClick={onClose}>
      <div class="modal card" onClick={(e) => e.stopPropagation()}>
        <div class="modal-head">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
            周报只读副本 · {copy.week_key}
          </h2>
          <button type="button" class="secondary" onClick={onClose}>
            关闭
          </button>
        </div>
        <p class="frozen-line">
          🔒 只读副本 · {copy.week_start} 至 {copy.week_end} · 签出人 {copy.checked_out_by} ·
          签出于 {new Date(copy.checked_out_at).toLocaleString()}
        </p>
        <p class="sub" style={{ marginTop: 0 }}>
          副本数字在签出时冻结，之后新办结的读数只会进入在线汇总，不会改动本副本。
        </p>
        <SummaryTiles summary={copy} />
      </div>
    </div>
  );
}

export function WeeklyReport({ authHeaders, isWriter }) {
  const [week, setWeek] = useState(currentWeekKey);
  const [summary, setSummary] = useState(null);
  const [checkouts, setCheckouts] = useState([]);
  const [copy, setCopy] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const loadSummary = useCallback(async () => {
    const res = await fetch(`/api/reports/weekly?week=${encodeURIComponent(week)}`, {
      headers: authHeaders(),
    });
    if (!res.ok) return;
    setSummary(await res.json());
  }, [week, authHeaders]);

  const loadCheckouts = useCallback(async () => {
    const res = await fetch("/api/reports/weekly/checkouts", { headers: authHeaders() });
    if (!res.ok) return;
    setCheckouts(await res.json());
  }, [authHeaders]);

  useEffect(() => {
    setError("");
    setMsg("");
    setSummary(null); // 切周瞬间清掉上一周数字，避免误读
    loadSummary();
    loadCheckouts();
    // 在线汇总与已签出列表持续刷新；已打开的副本不在此轮询范围内，数字不会被新数据带走。
    const t = setInterval(() => {
      loadSummary();
      loadCheckouts();
    }, 3000);
    return () => clearInterval(t);
  }, [loadSummary, loadCheckouts]);

  async function onCheckout() {
    setError("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fetch("/api/reports/weekly/checkout", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ week }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "签出失败");
        return;
      }
      setMsg(`已签出 ${week} 周报副本`);
      await loadSummary();
      await loadCheckouts();
    } finally {
      setBusy(false);
    }
  }

  async function openCopy(id) {
    setError("");
    const res = await fetch(`/api/reports/weekly/checkouts/${id}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      setError("副本加载失败");
      return;
    }
    // 只取这一次：打开后不再轮询，副本保持签出当周数字。
    setCopy(await res.json());
  }

  return (
    <div>
      <div class="card">
        <div class="report-head">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>周报签出</h2>
          <label class="week-pick">
            选择周
            <input
              type="week"
              value={week}
              onInput={(e) => setWeek(e.target.value)}
            />
          </label>
        </div>
        {summary && (
          <p class="sub" style={{ marginBottom: "0.75rem" }}>
            {summary.week_start} 至 {summary.week_end} ·{" "}
            {summary.frozen ? (
              <span class="frozen-badge">已签出 · 在线汇总仍在更新</span>
            ) : (
              <span>在线汇总（随新办结实时刷新）</span>
            )}
          </p>
        )}
        {summary ? (
          <SummaryTiles summary={summary} />
        ) : (
          <p class="sub">加载中…</p>
        )}
        <div class="checkout-row">
          {isWriter ? (
            summary?.frozen ? (
              <button type="button" disabled>
                该周已签出
              </button>
            ) : (
              <button type="button" disabled={busy} onClick={onCheckout}>
                {busy ? "签出中…" : `签出 ${week} 周报`}
              </button>
            )
          ) : (
            <p class="sub" style={{ margin: 0 }}>
              值班员为只读视角，可查看在线汇总与已签出副本，但不能签出。
            </p>
          )}
          {summary?.frozen && (
            <button type="button" class="secondary" onClick={() => openCopy(summary.checkout_id)}>
              查看该周只读副本
            </button>
          )}
        </div>
        {error && <p class="err">{error}</p>}
        {msg && <p class="ok">{msg}</p>}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>已签出副本</h2>
        <table>
          <thead>
            <tr>
              <th>周</th>
              <th>区间</th>
              <th>合格量</th>
              <th>超温量</th>
              <th>合格率</th>
              <th>签出人</th>
              <th>签出时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {checkouts.map((c) => (
              <tr key={c.id}>
                <td>{c.week_key}</td>
                <td>
                  {c.week_start} ~ {c.week_end}
                </td>
                <td>{c.pass_count}</td>
                <td>{c.fail_count}</td>
                <td>{c.pass_percent}%</td>
                <td>{c.checked_out_by}</td>
                <td>{new Date(c.checked_out_at).toLocaleString()}</td>
                <td>
                  <button type="button" class="secondary" onClick={() => openCopy(c.id)}>
                    打开副本
                  </button>
                </td>
              </tr>
            ))}
            {checkouts.length === 0 && (
              <tr>
                <td colspan="8">尚未签出任何周报</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <CopyModal copy={copy} onClose={() => setCopy(null)} />
    </div>
  );
}
