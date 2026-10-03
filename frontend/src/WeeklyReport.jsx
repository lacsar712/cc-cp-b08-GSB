import { useCallback, useEffect, useState } from "preact/hooks";

function todayStr() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function SummaryStats({ data }) {
  return (
    <div class="stats">
      <div class="stat">
        <span class="num">{data.qualified_count}</span>
        <span class="label">合格量</span>
      </div>
      <div class="stat">
        <span class="num">{data.overtemp_count}</span>
        <span class="label">超温量</span>
      </div>
      <div class="stat">
        <span class="num">{data.total_count}</span>
        <span class="label">合计</span>
      </div>
      <div class="stat">
        <span class="num">{data.qualified_ratio}%</span>
        <span class="label">合格率</span>
      </div>
      <div class="stat">
        <span class="num">{data.overtemp_ratio}%</span>
        <span class="label">超温占比</span>
      </div>
    </div>
  );
}

export function WeeklyReport({ user, authHeaders }) {
  const isWriter = user?.role === "writer";
  const [weekOf, setWeekOf] = useState(todayStr);
  const [summary, setSummary] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  // 在线汇总：数字完全来自后台接口，页面不做任何加总
  const loadSummary = useCallback(async () => {
    if (!weekOf) return;
    const res = await fetch(
      `/api/weekly-report/summary?week_of=${encodeURIComponent(weekOf)}`,
      { headers: authHeaders() }
    );
    if (res.ok) {
      setSummary(await res.json());
    }
  }, [weekOf, authHeaders]);

  // 已签出副本：只在进入页面与签出后加载，旧副本不会被后续办结改动
  const loadSnapshots = useCallback(async () => {
    const res = await fetch("/api/weekly-report/snapshots", {
      headers: authHeaders(),
    });
    if (res.ok) {
      const data = await res.json();
      setSnapshots(data);
      setSelectedId((cur) =>
        cur != null && data.some((s) => s.id === cur) ? cur : (data[0]?.id ?? null)
      );
    }
  }, [authHeaders]);

  useEffect(() => {
    loadSummary();
    const t = setInterval(loadSummary, 3000);
    return () => clearInterval(t);
  }, [loadSummary]);

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  async function checkout() {
    setError("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fetch("/api/weekly-report/checkout", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ week_of: weekOf }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "签出失败");
        if (res.status === 409) await loadSnapshots();
        return;
      }
      setMsg(data.message || `已签出 ${data.week_start} ~ ${data.week_end} 周报`);
      setSelectedId(data.id);
      await loadSnapshots();
    } finally {
      setBusy(false);
    }
  }

  const selected = snapshots.find((s) => s.id === selectedId) || null;

  return (
    <div>
      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>周报签出</h2>
        <div class="row">
          <label>
            周选择（任选周内一天）
            <input
              type="date"
              value={weekOf}
              onInput={(e) => setWeekOf(e.target.value)}
            />
          </label>
          {isWriter ? (
            <button type="button" onClick={checkout} disabled={busy || !summary || !weekOf}>
              签出本周周报
            </button>
          ) : (
            <span class="sub" style={{ margin: 0 }}>
              值班员仅可查看，不能签出
            </span>
          )}
        </div>
        {summary && (
          <div style={{ marginTop: "1rem" }}>
            <p class="sub" style={{ marginBottom: "0.5rem" }}>
              在线汇总 · 周范围 {summary.week_start} ~ {summary.week_end}
            </p>
            <SummaryStats data={summary} />
            <p class="sub" style={{ marginBottom: 0, marginTop: "0.5rem" }}>
              在线汇总由后台实时统计，新办结读数会自动刷新此处数字。
            </p>
          </div>
        )}
        {error && <p class="err">{error}</p>}
        {msg && <p class="ok">{msg}</p>}
      </div>

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>已签出区（只读副本）</h2>
        <table>
          <thead>
            <tr>
              <th>周次</th>
              <th>合格量</th>
              <th>超温量</th>
              <th>合计</th>
              <th>合格率</th>
              <th>超温占比</th>
              <th>签出人</th>
              <th>签出时间</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((s) => (
              <tr
                key={s.id}
                class={s.id === selectedId ? "selected" : ""}
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedId(s.id)}
              >
                <td>
                  {s.week_start} ~ {s.week_end}
                </td>
                <td>{s.qualified_count}</td>
                <td>{s.overtemp_count}</td>
                <td>{s.total_count}</td>
                <td>{s.qualified_ratio}%</td>
                <td>{s.overtemp_ratio}%</td>
                <td>{s.created_by}</td>
                <td>{formatTime(s.created_at)}</td>
              </tr>
            ))}
            {snapshots.length === 0 && (
              <tr>
                <td colspan="8">暂无已签出副本</td>
              </tr>
            )}
          </tbody>
        </table>
        {selected && (
          <div class="snapshot-detail">
            <h3 style={{ marginTop: 0, fontSize: "1rem" }}>
              周报副本（只读）· {selected.week_start} ~ {selected.week_end}
            </h3>
            <SummaryStats data={selected} />
            <p class="sub" style={{ marginBottom: 0, marginTop: "0.5rem" }}>
              副本在签出时刻冻结，之后新办结的读数只刷新在线汇总，不影响此处数字。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
