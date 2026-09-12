const videoInput = document.getElementById("videoInput");
const videoPlayer = document.getElementById("videoPlayer");
const analyzeBtn = document.getElementById("analyzeBtn");
const statusBox = document.getElementById("status");
const usageBox = document.getElementById("usageBox");
const fileInfo = document.getElementById("fileInfo");
const results = document.getElementById("results");

let selectedFile = null;
let currentAnalysis = null;
let selectedOverallRating = null;
let premiumUnlimited = false;

function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "--";
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    const mb = kb / 1024;
    if (mb < 1024) return `${mb.toFixed(1)} MB`;
    const gb = mb / 1024;
    return `${gb.toFixed(2)} GB`;
}

async function readJsonResponse(response, label = "서버") {
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    const text = await response.text();

    if (contentType.includes("application/json")) {
        try {
            return text ? JSON.parse(text) : {};
        } catch (_) {
            throw new Error(`${label}가 잘못된 JSON을 반환했습니다. (HTTP ${response.status})`);
        }
    }

    const looksLikeHtml = /^\s*<!doctype\s+html|^\s*<html/i.test(text);
    if (looksLikeHtml) {
        if (response.status === 502) {
            throw new Error("서버가 일시적으로 응답하지 못했습니다. Render 로그에서 502/SIGKILL 여부를 확인해 주세요.");
        }
        if (response.status === 503) {
            throw new Error("서버가 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.");
        }
        throw new Error(`${label}가 HTML 오류 페이지를 반환했습니다. (HTTP ${response.status})`);
    }

    const preview = text.trim().replace(/\s+/g, " ").slice(0, 180);
    throw new Error(
        preview
            ? `${label} 응답 오류 (HTTP ${response.status}): ${preview}`
            : `${label} 응답 오류 (HTTP ${response.status})`
    );
}

function resetOverallFeedback() {
    selectedOverallRating = null;

    document.querySelectorAll(".feedback-choice").forEach(button => {
        button.classList.remove("selected");
    });

    document.querySelectorAll(".categories input[type='checkbox']").forEach(input => {
        input.checked = false;
    });

    const comment = document.getElementById("overallComment");
    if (comment) comment.value = "";

    const submit = document.getElementById("submitOverallFeedback");
    if (submit) submit.disabled = true;

    const message = document.getElementById("overallFeedbackStatus");
    if (message) message.textContent = "";
}

async function refreshUsage() {
    try {
        const r = await fetch("/usage", {credentials: "same-origin"});
        const data = await readJsonResponse(r, "사용량 서버");

        if (r.status === 401) {
            premiumUnlimited = false;
            usageBox.innerHTML = '분석하려면 <a href="/login">로그인</a>해 주세요.';
            usageBox.classList.remove("premium-usage");
            analyzeBtn.disabled = true;
            return;
        }

        if (!r.ok) {
            const detail = data && data.detail;
            if (detail && typeof detail === "object") {
                throw new Error(detail.message || detail.code || `HTTP ${r.status}`);
            }
            throw new Error(detail || `사용 가능 횟수 확인 실패 (HTTP ${r.status})`);
        }

        if (data.premium && data.unlimited) {
            premiumUnlimited = true;
            usageBox.textContent = "PREMIUM · 분석 횟수 무제한 · 광고 없음";
            usageBox.classList.add("premium-usage");
            analyzeBtn.disabled = !selectedFile;
            return;
        }

        premiumUnlimited = false;
        usageBox.classList.remove("premium-usage");

        const accountRemaining = data.account_remaining ?? data.remaining;
        const accountLimit = data.account_limit ?? data.daily_limit;
        const ipRemaining = data.ip_remaining;
        const ipLimit = data.ip_limit;

        if (typeof ipRemaining === "number" && typeof ipLimit === "number") {
            usageBox.textContent = `계정 남은 분석: ${accountRemaining} / ${accountLimit} · 동일 네트워크 남은 분석: ${ipRemaining} / ${ipLimit}`;
        } else {
            usageBox.textContent = `내 오늘 남은 무료 분석: ${data.remaining} / ${data.daily_limit}`;
        }

        analyzeBtn.disabled = !selectedFile || data.remaining <= 0;
    } catch (e) {
        premiumUnlimited = false;
        usageBox.textContent = `사용 가능 횟수를 확인하지 못했습니다: ${e.message}`;
        usageBox.classList.remove("premium-usage");
        analyzeBtn.disabled = true;
    }
}

videoInput.addEventListener("change", async () => {
    selectedFile = videoInput.files[0] || null;
    currentAnalysis = null;
    resetOverallFeedback();

    if (selectedFile) {
        if (fileInfo) {
            fileInfo.textContent = `${selectedFile.name} · ${formatFileSize(selectedFile.size)}`;
        }
        videoPlayer.src = URL.createObjectURL(selectedFile);
        videoPlayer.style.display = "block";
        results.classList.add("hidden");
    } else {
        if (fileInfo) fileInfo.textContent = "선택된 영상 없음";
        videoPlayer.removeAttribute("src");
        videoPlayer.style.display = "none";
        results.classList.add("hidden");
    }

    await refreshUsage();
});

function timestampToSeconds(ts) {
    const p = ts.trim().split(":").map(Number);
    if (p.length === 2) return p[0] * 60 + p[1];
    if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
    return 0;
}

async function sendFeedback(payload) {
    const r = await fetch("/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify(payload)
    });
    const data = await readJsonResponse(r, "피드백 서버");
    if (!r.ok) {
        const detail = data.detail;
        if (detail && typeof detail === "object") {
            throw new Error(detail.message || "피드백 저장 실패");
        }
        throw new Error(detail || "피드백 저장 실패");
    }
    return data;
}

function renderScores(scores) {
    const labels = {
        aim: "조준 (Aim)",
        movement: "움직임 (Movement)",
        positioning: "포지셔닝",
        utility: "스킬 활용",
        decision_making: "판단력"
    };
    const root = document.getElementById("scoreBars");
    root.innerHTML = "";

    for (const [key, label] of Object.entries(labels)) {
        const value = scores[key] ?? 0;
        const row = document.createElement("div");
        row.className = "score-row";

        const name = document.createElement("div");
        name.textContent = label;

        const bar = document.createElement("div");
        bar.className = "bar";
        const fill = document.createElement("div");
        fill.style.width = `${value}%`;
        bar.appendChild(fill);

        const score = document.createElement("div");
        score.textContent = value;

        row.append(name, bar, score);
        root.appendChild(row);
    }
}

function renderTier(prediction) {
    const tierBadge = document.getElementById("tierBadge");
    const tierConfidence = document.getElementById("tierConfidence");
    const tierReason = document.getElementById("tierReason");

    if (!prediction || !prediction.tier) {
        tierBadge.className = "tier-badge";
        tierBadge.textContent = "판단 불가";
        tierConfidence.textContent = "신뢰도 --%";
        tierReason.textContent = "이 클립에서는 티어를 추정하기 위한 정보가 부족합니다.";
        return;
    }

    const labels = {
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

    const tier = prediction.tier;
    const tierClass = `tier-${String(tier).toLowerCase()}`;
    const confidence = Math.max(0, Math.min(100, Math.round((prediction.confidence ?? 0) * 100)));

    tierBadge.className = `tier-badge ${tierClass}`;
    tierBadge.textContent = labels[tier] || tier;
    tierConfidence.textContent = `신뢰도 ${confidence}%`;
    tierReason.textContent = prediction.reason || "클립에서 관찰된 플레이를 종합해 추정했습니다.";
}

function renderEvents(events) {
    const root = document.getElementById("events");
    root.innerHTML = "";

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

    events.forEach((event, index) => {
        const box = document.createElement("div");
        box.className = "event";

        const head = document.createElement("div");
        head.className = "event-head";

        const ts = document.createElement("button");
        ts.className = "timestamp";
        ts.textContent = event.timestamp;
        ts.onclick = () => {
            videoPlayer.currentTime = timestampToSeconds(event.timestamp);
            videoPlayer.play();
            videoPlayer.scrollIntoView({behavior: "smooth", block: "center"});
        };

        head.appendChild(ts);
        for (const value of [
            categoryLabels[event.category] || event.category,
            severityLabels[event.severity] || event.severity,
            `신뢰도 ${Math.round(event.confidence * 100)}%`
        ]) {
            const badge = document.createElement("span");
            badge.className = "badge";
            badge.textContent = value;
            head.appendChild(badge);
        }

        const observation = document.createElement("p");
        observation.textContent = `관찰: ${event.observation}`;

        const feedback = document.createElement("p");
        feedback.textContent = `피드백: ${event.feedback}`;

        const vote = document.createElement("div");
        vote.className = "event-feedback";
        const label = document.createElement("span");
        label.className = "muted";
        label.textContent = "이 피드백은 정확했나요?";

        const up = document.createElement("button");
        up.textContent = "👍 맞음";
        const down = document.createElement("button");
        down.textContent = "👎 틀림";
        const msg = document.createElement("span");
        msg.className = "muted";

        async function submit(rating, button) {
            try {
                await sendFeedback({
                    analysis_id: currentAnalysis.analysis_id,
                    target_type: "event",
                    event_index: index,
                    event_timestamp: event.timestamp,
                    event_category: event.category,
                    rating,
                    categories: [event.category],
                    comment: ""
                });
                up.classList.remove("selected");
                down.classList.remove("selected");
                button.classList.add("selected");
                msg.textContent = "저장됨";
            } catch (e) {
                msg.textContent = e.message;
            }
        }

        up.onclick = () => submit("up", up);
        down.onclick = () => submit("down", down);

        vote.append(label, up, down, msg);
        box.append(head, observation, feedback, vote);
        root.appendChild(box);
    });
}

function renderResult(data) {
    resetOverallFeedback();
    currentAnalysis = data;

    document.getElementById("overallScore").textContent = data.overall_score;
    document.getElementById("summary").textContent = data.summary;
    renderTier(data.tier_prediction);
    renderScores(data.scores);
    renderEvents(data.events || []);

    const priorities = document.getElementById("priorities");
    priorities.innerHTML = "";
    for (const item of data.top_priorities || []) {
        const li = document.createElement("li");
        li.textContent = item;
        priorities.appendChild(li);
    }

    const limitations = document.getElementById("limitations");
    limitations.innerHTML = "";
    for (const item of data.limitations || []) {
        const li = document.createElement("li");
        li.textContent = item;
        limitations.appendChild(li);
    }

    results.classList.remove("hidden");
}

analyzeBtn.addEventListener("click", async () => {
    if (!selectedFile) return;

    analyzeBtn.disabled = true;
    statusBox.textContent = "영상 업로드 및 AI 분석 중...";

    const form = new FormData();
    form.append("file", selectedFile);

    try {
        const r = await fetch("/analyze", {
            method: "POST",
            credentials: "same-origin",
            body: form
        });
        const data = await readJsonResponse(r, "분석 서버");

        if (!r.ok) {
            const detail = data.detail;
            if (detail && typeof detail === "object") {
                if (detail.code === "LOGIN_REQUIRED") {
                    statusBox.innerHTML = '로그인이 필요합니다. <a href="/login">로그인하기</a>';
                    return;
                }
                throw new Error(detail.message || detail.code || "분석 실패");
            }
            throw new Error(detail || "분석 실패");
        }

        renderResult(data);
        statusBox.textContent = data.usage && data.usage.premium
            ? `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS · PREMIUM`
            : `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS`;
    } catch (e) {
        statusBox.textContent = `오류: ${e.message}`;
    } finally {
        await refreshUsage();
    }
});

document.querySelectorAll(".feedback-choice").forEach(button => {
    button.addEventListener("click", () => {
        selectedOverallRating = button.dataset.rating;
        document.querySelectorAll(".feedback-choice").forEach(b => b.classList.remove("selected"));
        button.classList.add("selected");
        document.getElementById("submitOverallFeedback").disabled = !currentAnalysis;
    });
});

document.getElementById("submitOverallFeedback").addEventListener("click", async () => {
    if (!currentAnalysis || !selectedOverallRating) return;

    const checked = Array.from(document.querySelectorAll(".categories input:checked")).map(x => x.value);
    const msg = document.getElementById("overallFeedbackStatus");

    try {
        await sendFeedback({
            analysis_id: currentAnalysis.analysis_id,
            target_type: "overall",
            event_index: null,
            event_timestamp: null,
            event_category: null,
            rating: selectedOverallRating,
            categories: checked,
            comment: document.getElementById("overallComment").value.trim()
        });
        msg.textContent = "피드백이 저장되었습니다.";
    } catch (e) {
        msg.textContent = `오류: ${e.message}`;
    }
});