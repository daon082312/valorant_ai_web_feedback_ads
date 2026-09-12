const historyStatus = document.getElementById("historyStatus");
const historyList = document.getElementById("historyList");
const historyDetail = document.getElementById("historyDetail");
const historyCloseDetail = document.getElementById("historyCloseDetail");

const tierLabels = {
    Iron: "아이언",
    Bronze: "브론즈",
    Silver: "실버",
    Gold: "골드",
    Platinum: "플래티넘",
    Diamond: "다이아몬드",
    Ascendant: "초월자",
    Immortal: "불멸",
    Radiant: "레디언트"
};

const categoryLabels = {
    aim: "조준",
    movement: "움직임",
    positioning: "포지셔닝",
    utility: "스킬 활용",
    decision_making: "판단력",
    teamplay: "팀플레이",
    other: "기타"
};

const severityLabels = {
    positive: "잘한 점",
    low: "낮은 중요도",
    medium: "중간 중요도",
    high: "높은 중요도"
};

function escapeText(value) {
    return String(value ?? "");
}

function formatDate(value) {
    if (!value) return "날짜 없음";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("ko-KR", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit"
    }).format(date);
}

function create(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

function renderEmpty() {
    historyList.innerHTML = "";
    const box = create("div", "history-empty");
    box.append(
        create("h3", "", "저장된 분석이 없습니다."),
        create("p", "muted", "새 영상을 분석하면 성공한 결과가 자동으로 저장됩니다.")
    );
    historyList.appendChild(box);
}

async function apiJson(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers: {
            ...(options.headers || {})
        }
    });

    let data = {};
    try {
        data = await response.json();
    } catch (_) {
        throw new Error(`서버 응답 오류 (HTTP ${response.status})`);
    }

    if (!response.ok) {
        throw new Error(data.detail || `요청 실패 (HTTP ${response.status})`);
    }
    return data;
}

function renderList(items) {
    historyList.innerHTML = "";
    if (!items.length) {
        renderEmpty();
        return;
    }

    for (const item of items) {
        const card = create("article", "history-card");
        const top = create("div", "history-card-top");
        const left = create("div");
        left.append(
            create("div", "history-file-name", item.file_name || "영상"),
            create("div", "history-card-meta muted", formatDate(item.created_at))
        );
        top.append(left);

        const stats = create("div", "history-card-stats");
        stats.append(
            create("span", "history-pill", `점수 ${item.overall_score ?? "--"}`),
            create("span", "history-pill", tierLabels[item.tier] || item.tier || "티어 미정")
        );

        const summary = create("p", "history-card-summary", item.summary || "요약 없음");
        const actions = create("div", "history-actions");
        const detailBtn = create("button", "history-secondary", "상세 보기");
        detailBtn.type = "button";
        detailBtn.onclick = () => showDetail(item.analysis_id);

        const deleteBtn = create("button", "history-danger", "삭제");
        deleteBtn.type = "button";
        deleteBtn.onclick = () => deleteItem(item.analysis_id, item.file_name || "이 분석");

        actions.append(detailBtn, deleteBtn);
        card.append(top, stats, summary, actions);
        historyList.appendChild(card);
    }
}

function renderScores(scores) {
    const root = document.getElementById("historyScores");
    root.innerHTML = "";
    const keys = ["aim", "movement", "positioning", "utility", "decision_making"];

    for (const key of keys) {
        const value = Number(scores?.[key] ?? 0);
        const row = create("div", "history-score-row");
        row.appendChild(create("div", "", categoryLabels[key] || key));

        const track = create("div", "history-score-track");
        const fill = create("div", "history-score-fill");
        fill.style.width = `${Math.max(0, Math.min(100, value))}%`;
        track.appendChild(fill);

        row.append(track, create("div", "", String(value)));
        root.appendChild(row);
    }
}

function renderEvents(events) {
    const root = document.getElementById("historyEvents");
    root.innerHTML = "";

    if (!Array.isArray(events) || !events.length) {
        root.appendChild(create("p", "muted", "저장된 주요 장면이 없습니다."));
        return;
    }

    for (const event of events) {
        const box = create("div", "history-event");
        const head = create("div", "history-event-head");
        head.append(
            create("span", "history-pill", event.timestamp || "--:--"),
            create("span", "history-pill", categoryLabels[event.category] || event.category || "기타"),
            create("span", "history-pill", severityLabels[event.severity] || event.severity || "")
        );
        const confidence = Math.round(Number(event.confidence || 0) * 100);
        head.appendChild(create("span", "history-pill", `신뢰도 ${confidence}%`));

        box.append(
            head,
            create("p", "", `관찰: ${escapeText(event.observation)}`),
            create("p", "", `피드백: ${escapeText(event.feedback)}`)
        );
        root.appendChild(box);
    }
}

function renderListItems(rootId, items) {
    const root = document.getElementById(rootId);
    root.innerHTML = "";
    if (!Array.isArray(items) || !items.length) {
        const li = create("li", "muted", "없음");
        root.appendChild(li);
        return;
    }
    for (const item of items) {
        root.appendChild(create("li", "", item));
    }
}

async function showDetail(analysisId) {
    historyStatus.textContent = "분석 상세를 불러오는 중...";
    try {
        const item = await apiJson(`/api/history/${encodeURIComponent(analysisId)}`);
        const data = item.result || {};

        document.getElementById("historyDetailTitle").textContent = item.file_name || "분석 상세";
        document.getElementById("historyDetailMeta").textContent = `${formatDate(item.created_at)} · ${data.model_used || "Gemini"}`;
        document.getElementById("historyOverallScore").textContent = data.overall_score ?? "--";
        document.getElementById("historyTier").textContent = tierLabels[data.tier_prediction?.tier] || data.tier_prediction?.tier || "--";
        document.getElementById("historySummary").textContent = data.summary || "요약 없음";

        renderScores(data.scores || {});
        renderEvents(data.events || []);
        renderListItems("historyPriorities", data.top_priorities || []);
        renderListItems("historyLimitations", data.limitations || []);

        historyDetail.classList.remove("hidden");
        historyDetail.scrollIntoView({behavior: "smooth", block: "start"});
        historyStatus.textContent = "";
    } catch (e) {
        historyStatus.textContent = `오류: ${e.message}`;
    }
}

async function deleteItem(analysisId, fileName) {
    if (!window.confirm(`${fileName} 기록을 삭제할까요?`)) return;

    historyStatus.textContent = "삭제 중...";
    try {
        await apiJson(`/api/history/${encodeURIComponent(analysisId)}`, {method: "DELETE"});
        historyDetail.classList.add("hidden");
        await loadHistory();
    } catch (e) {
        historyStatus.textContent = `오류: ${e.message}`;
    }
}

async function loadHistory() {
    historyStatus.textContent = "저장된 분석을 불러오는 중...";
    try {
        const data = await apiJson("/api/history?limit=50");
        const items = Array.isArray(data.items) ? data.items : [];
        renderList(items);
        historyStatus.textContent = items.length
            ? `저장된 분석 ${items.length}개`
            : "저장된 분석이 없습니다.";
    } catch (e) {
        historyList.innerHTML = "";
        historyStatus.textContent = `오류: ${e.message}`;
    }
}

historyCloseDetail?.addEventListener("click", () => {
    historyDetail.classList.add("hidden");
});

loadHistory();
