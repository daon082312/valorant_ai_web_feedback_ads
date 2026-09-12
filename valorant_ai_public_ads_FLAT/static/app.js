const videoInput = document.getElementById("videoInput");
const videoPlayer = document.getElementById("videoPlayer");
const analyzeBtn = document.getElementById("analyzeBtn");
const statusBox = document.getElementById("status");
const usageBox = document.getElementById("usageBox");
const fileInfo = document.getElementById("fileInfo");
const results = document.getElementById("results");
const saveAnalysisBtn = document.getElementById("saveAnalysisBtn");
const saveAnalysisStatus = document.getElementById("saveAnalysisStatus");

let selectedFile = null;
let currentAnalysis = null;
let selectedOverallRating = null;
let premiumUnlimited = false;
let analysisInProgress = false;
let currentAgentAbilities = [];
let valorantCatalogAgents = [];

function setAnalysisInProgress(active) {
    analysisInProgress = Boolean(active);
    document.documentElement.classList.toggle("analysis-in-progress", analysisInProgress);
}

window.addEventListener("beforeunload", (event) => {
    if (!analysisInProgress) return;
    event.preventDefault();
    event.returnValue = "";
});

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

function resetSaveControls() {
    if (saveAnalysisBtn) saveAnalysisBtn.disabled = true;
    if (saveAnalysisStatus) saveAnalysisStatus.textContent = "";
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

async function fetchValorantCatalog(agent = "") {
    const suffix = agent ? `?agent=${encodeURIComponent(agent)}` : "";
    const response = await fetch(`/vision/catalog${suffix}`, {credentials: "same-origin"});
    const data = await readJsonResponse(response, "VALORANT UI 기준 데이터");
    if (!response.ok) throw new Error(data.detail || "요원/스킬 기준 데이터를 불러오지 못했습니다.");
    return data;
}

async function populateAgentOptions() {
    try {
        const data = await fetchValorantCatalog();
        valorantCatalogAgents = Array.isArray(data.agents) ? data.agents : [];
        const list = document.getElementById("agentOptions");
        if (!list) return;
        list.innerHTML = "";
        for (const agent of valorantCatalogAgents) {
            const option = document.createElement("option");
            option.value = agent.name;
            list.appendChild(option);
        }
    } catch (error) {
        console.warn("VALORANT 요원 목록 로드 생략", error);
    }
}

async function fetchAgentKit(agent) {
    const name = String(agent || "").trim();
    if (!name || name === "Unknown") return [];

    const cached = valorantCatalogAgents.find(
        item => String(item.name || "").toLowerCase() === name.toLowerCase()
    );
    if (cached && Array.isArray(cached.abilities)) return cached.abilities;

    try {
        const data = await fetchValorantCatalog(name);
        return Array.isArray(data.abilities) ? data.abilities : [];
    } catch (error) {
        console.warn("VALORANT 스킬 목록 로드 생략", error);
        return [];
    }
}

function canonicalFromList(value, allowed) {
    const wanted = String(value || "").trim().toLowerCase();
    if (!wanted) return null;
    return (allowed || []).find(item => String(item).toLowerCase() === wanted) || null;
}

videoInput.addEventListener("change", async () => {
    selectedFile = videoInput.files[0] || null;
    currentAnalysis = null;
    currentAgentAbilities = [];
    resetOverallFeedback();
    resetSaveControls();

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
    const p = String(ts || "").trim().split(":").map(Number);
    if (p.length === 2) return p[0] * 60 + p[1];
    if (p.length === 3) return p[0] * 3600 + p[1] * 60 + p[2];
    return 0;
}

function waitForEventOnce(target, eventName, timeoutMs = 3000) {
    return new Promise((resolve, reject) => {
        let timer = null;
        const done = () => {
            if (timer) clearTimeout(timer);
            target.removeEventListener(eventName, done);
            resolve();
        };
        target.addEventListener(eventName, done, {once: true});
        timer = setTimeout(() => {
            target.removeEventListener(eventName, done);
            reject(new Error("영상 프레임을 준비하지 못했습니다."));
        }, timeoutMs);
    });
}

async function captureFrameAt(seconds) {
    if (!videoPlayer.src) throw new Error("영상이 없습니다.");

    if (videoPlayer.readyState < 1) {
        await waitForEventOnce(videoPlayer, "loadedmetadata", 5000);
    }

    const duration = Number(videoPlayer.duration || 0);
    const target = Math.max(0, Math.min(Number(seconds || 0), duration > 0 ? Math.max(0, duration - 0.05) : Number(seconds || 0)));
    const previousTime = Number(videoPlayer.currentTime || 0);
    const wasPaused = videoPlayer.paused;

    if (Math.abs(previousTime - target) > 0.04) {
        videoPlayer.pause();
        videoPlayer.currentTime = target;
        await waitForEventOnce(videoPlayer, "seeked", 4000);
    }

    const sourceWidth = videoPlayer.videoWidth || 1280;
    const sourceHeight = videoPlayer.videoHeight || 720;
    const width = Math.min(640, sourceWidth);
    const height = Math.max(1, Math.round(sourceHeight * (width / sourceWidth)));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d", {alpha: false});
    ctx.drawImage(videoPlayer, 0, 0, width, height);
    const imageData = canvas.toDataURL("image/jpeg", 0.72);

    if (Math.abs(previousTime - target) > 0.04) {
        videoPlayer.currentTime = Math.max(0, Math.min(previousTime, duration || previousTime));
    }
    if (!wasPaused) {
        videoPlayer.pause();
    }

    return imageData;
}

async function visionRequest(path, payload) {
    const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify(payload)
    });
    const data = await readJsonResponse(response, "학습 서버");
    if (!response.ok) {
        throw new Error(data.detail || `학습 서버 오류 (HTTP ${response.status})`);
    }
    return data;
}

async function predictVision(targetType, seconds, agentLabel = "") {
    try {
        const imageData = await captureFrameAt(seconds);
        const prediction = await visionRequest("/vision/predict", {
            target_type: targetType,
            image_data: imageData,
            agent_label: agentLabel || ""
        });
        if (!prediction.ready) {
            return {ready: false, reason: prediction.reason || "needs_more_labels", ...prediction};
        }
        return prediction;
    } catch (error) {
        console.warn("학습/UI 기준 모델 예측 생략", error);
        return {ready: false, reason: "prediction_failed"};
    }
}

async function trainVision({targetType, label, seconds, eventIndex, predictedLabel}) {
    if (!currentAnalysis?.analysis_id) throw new Error("분석 ID가 없습니다.");
    const cleanLabel = String(label || "").trim();
    if (!cleanLabel) throw new Error("정답 이름을 입력해 주세요.");

    const imageData = await captureFrameAt(seconds);
    return await visionRequest("/vision/train", {
        target_type: targetType,
        image_data: imageData,
        label: cleanLabel,
        analysis_id: currentAnalysis.analysis_id,
        event_index: eventIndex,
        predicted_label: predictedLabel || "",
        agent_label: targetType === "skill" ? (currentAnalysis.agent_prediction?.agent || "") : ""
    });
}

function agentSampleTime(data) {
    const firstEvent = Array.isArray(data?.events) && data.events.length ? data.events[0] : null;
    if (firstEvent?.timestamp) return timestampToSeconds(firstEvent.timestamp);
    const duration = Number(videoPlayer.duration || 0);
    return duration > 0 ? Math.min(1.0, duration * 0.25) : 0;
}

async function applyLearnedVisionPredictions(data) {
    if (!data || !videoPlayer.src) return data;

    const agentPred = await predictVision("agent", agentSampleTime(data));
    data.learned_agent_prediction = agentPred;

    const referenceBased = String(agentPred.source || "").includes("valorant_ui_reference");
    const agentPredictionUsable = agentPred.ready && (
        (referenceBased && Number(agentPred.confidence || 0) >= 0.68) ||
        (Number(agentPred.sample_count || 0) >= 2 && Number(agentPred.confidence || 0) >= 0.60)
    );

    if (
        agentPredictionUsable &&
        (
            !data.agent_prediction ||
            data.agent_prediction.agent === "Unknown" ||
            Number(data.agent_prediction.confidence || 0) < 0.65 ||
            Number(agentPred.confidence || 0) >= 0.78
        )
    ) {
        const original = data.agent_prediction?.agent || "Unknown";
        data.agent_prediction = {
            agent: agentPred.label,
            confidence: Math.max(Number(data.agent_prediction?.confidence || 0), Number(agentPred.confidence || 0)),
            reason: referenceBased
                ? `VALORANT HUD 스킬 아이콘 기준 데이터로 ${agentPred.label}를 교차 확인했습니다. Gemini의 최초 판별은 ${original}였습니다.`
                : `사용자 정정 데이터로 학습된 이미지 모델이 ${agentPred.label}로 보정했습니다. Gemini의 최초 판별은 ${original}였습니다.`,
            learned_model: !referenceBased,
            reference_model: referenceBased,
            original_agent: original
        };
    }

    const finalAgent = data.agent_prediction?.agent || "";
    currentAgentAbilities = await fetchAgentKit(finalAgent);
    data.agent_ability_catalog = currentAgentAbilities;

    const events = Array.isArray(data.events) ? data.events : [];
    for (const event of events) {
        if (event.ability_name && currentAgentAbilities.length) {
            const canonical = canonicalFromList(event.ability_name, currentAgentAbilities);
            if (canonical) {
                event.ability_name = canonical;
            } else {
                event.original_ability_name = event.ability_name;
                event.ability_name = null;
                event.ability_confidence = 0;
                event.ability_removed_by_kit_validation = true;
            }
        }

        if (!(event.ability_name || event.category === "utility")) continue;
        const prediction = await predictVision(
            "skill",
            timestampToSeconds(event.timestamp),
            finalAgent
        );
        event.learned_skill_prediction = prediction;
        const canonicalPrediction = canonicalFromList(prediction.label, currentAgentAbilities);
        if (
            prediction.ready &&
            canonicalPrediction &&
            Number(prediction.sample_count || 0) >= 2 &&
            Number(prediction.confidence || 0) >= 0.72 &&
            (!event.ability_name || Number(event.ability_confidence || 0) < 0.70 || Number(prediction.confidence || 0) >= 0.82)
        ) {
            event.original_ability_name = event.ability_name || null;
            event.ability_name = canonicalPrediction;
            event.ability_confidence = prediction.confidence;
            event.ability_from_learned_model = true;
        }
    }
    return data;
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

async function saveAnalysisHistory(data, fileName) {
    try {
        const response = await fetch("/api/history", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            credentials: "same-origin",
            body: JSON.stringify({
                file_name: fileName || "영상",
                analysis: data
            })
        });

        const body = await readJsonResponse(response, "분석 저장 서버");
        if (!response.ok) {
            const detail = body && body.detail;
            const message = typeof detail === "object"
                ? (detail.message || JSON.stringify(detail))
                : (detail || `HTTP ${response.status}`);
            console.warn("분석 기록 저장 실패", response.status, message);
            return {ok: false, detail: message, status: response.status};
        }
        return {ok: true, analysis_id: body.analysis_id || data?.analysis_id || ""};
    } catch (error) {
        console.warn("분석 기록 저장 실패", error);
        return {ok: false, detail: error.message || "저장 요청 실패"};
    }
}

function renderFeedbackCalibration(data) {
    const root = document.getElementById("feedbackCalibrationNotice");
    if (!root) return;

    const count = Number(data.feedback_calibration_samples || 0);
    const used = Boolean(data.feedback_calibration_used);
    root.classList.toggle("active", used);

    if (used) {
        root.textContent = `피드백 보정 적용됨 · 최근 ${count}건의 평가 통계를 이번 분석에 반영했습니다.`;
    } else if (count > 0) {
        root.textContent = `피드백 ${count}건 수집됨 · 아직 보정 기준에 필요한 표본 수를 충족하지 않아 이번 분석에는 강제 적용하지 않았습니다.`;
    } else {
        root.textContent = "피드백 보정 데이터가 아직 없습니다. 평가가 쌓이면 다음 분석부터 통계적으로 반영됩니다.";
    }
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

function renderAgent(prediction) {
    const name = document.getElementById("agentPredictionName");
    const confidence = document.getElementById("agentPredictionConfidence");
    const reason = document.getElementById("agentPredictionReason");
    const input = document.getElementById("agentCorrectionInput");
    const status = document.getElementById("agentTrainingStatus");

    const agent = prediction?.agent || "Unknown";
    const conf = Math.round(Math.max(0, Math.min(1, Number(prediction?.confidence || 0))) * 100);
    name.textContent = agent === "Unknown" ? "판별 불가" : agent;
    const sourceText = prediction?.reference_model
        ? " · VALORANT UI 기준 보정"
        : prediction?.learned_model
            ? " · 사용자 학습 모델 보정"
            : "";
    confidence.textContent = `신뢰도 ${conf}%${sourceText}`;
    reason.textContent = prediction?.reason || "영상에서 에이전트를 확실히 판별하지 못했습니다.";
    input.value = agent === "Unknown" ? "" : agent;
    status.textContent = "틀렸다면 실제 에이전트를 선택하고 ‘정답으로 학습’을 누르세요. 이미지 원본은 저장하지 않습니다.";
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
            videoPlayer.pause();
            videoPlayer.currentTime = timestampToSeconds(event.timestamp);
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

        const abilityRow = document.createElement("div");
        abilityRow.className = "vision-ability-row";
        const abilityLabel = document.createElement("span");
        abilityLabel.className = "muted";
        abilityLabel.textContent = "스킬 판별:";
        const abilityName = document.createElement("span");
        abilityName.className = "vision-ability-name";
        abilityName.textContent = event.ability_name || "판별 안 됨";
        const abilityConf = document.createElement("span");
        abilityConf.className = `badge${event.ability_from_learned_model ? " vision-learned-badge" : ""}`;
        abilityConf.textContent = event.ability_name
            ? `신뢰도 ${Math.round(Number(event.ability_confidence || 0) * 100)}%${event.ability_from_learned_model ? " · 학습 모델" : ""}`
            : "--";
        abilityRow.append(abilityLabel, abilityName, abilityConf);

        const skillCorrection = document.createElement("div");
        skillCorrection.className = "vision-correction";
        const skillInput = document.createElement("input");
        skillInput.maxLength = 60;
        skillInput.placeholder = currentAgentAbilities.length
            ? "실제 스킬을 선택하세요"
            : "틀렸다면 실제 스킬명";
        skillInput.value = event.ability_name || "";

        if (currentAgentAbilities.length) {
            const dataListId = `skillOptions-${index}`;
            skillInput.setAttribute("list", dataListId);
            const list = document.createElement("datalist");
            list.id = dataListId;
            for (const ability of currentAgentAbilities) {
                const option = document.createElement("option");
                option.value = ability;
                list.appendChild(option);
            }
            skillCorrection.appendChild(list);
        }

        const skillTrain = document.createElement("button");
        skillTrain.type = "button";
        skillTrain.textContent = "정답으로 학습";
        const skillStatus = document.createElement("span");
        skillStatus.className = "vision-training-status";
        skillTrain.onclick = async () => {
            skillTrain.disabled = true;
            skillStatus.textContent = "학습 중...";
            try {
                if (currentAgentAbilities.length && !canonicalFromList(skillInput.value, currentAgentAbilities)) {
                    throw new Error(`${currentAnalysis.agent_prediction?.agent || "현재 요원"}의 실제 스킬 목록에서 선택해 주세요.`);
                }
                const result = await trainVision({
                    targetType: "skill",
                    label: skillInput.value,
                    seconds: timestampToSeconds(event.timestamp),
                    eventIndex: index,
                    predictedLabel: event.ability_name || ""
                });
                event.ability_name = result.label;
                abilityName.textContent = result.label;
                const samples = result.model?.sample_count ?? 0;
                const labels = result.model?.label_count ?? 0;
                skillStatus.textContent = `학습 완료 · 스킬 샘플 ${samples}개 / 라벨 ${labels}종`;
                await saveAnalysisHistory(currentAnalysis, selectedFile?.name || "영상");
            } catch (error) {
                skillStatus.textContent = `오류: ${error.message}`;
            } finally {
                skillTrain.disabled = false;
            }
        };
        skillCorrection.append(skillInput, skillTrain, skillStatus);

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
                msg.textContent = "저장됨 · 다음 분석부터 집계에 반영";
            } catch (e) {
                msg.textContent = e.message;
            }
        }

        up.onclick = () => submit("up", up);
        down.onclick = () => submit("down", down);

        vote.append(label, up, down, msg);
        box.append(head, observation, feedback, abilityRow, skillCorrection, vote);
        root.appendChild(box);
    });
}

function renderResult(data) {
    resetOverallFeedback();
    currentAnalysis = data;

    document.getElementById("overallScore").textContent = data.overall_score;
    document.getElementById("summary").textContent = data.summary;
    renderFeedbackCalibration(data);
    renderAgent(data.agent_prediction);
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

    if (saveAnalysisBtn) saveAnalysisBtn.disabled = !data.analysis_id;
    results.classList.remove("hidden");
}

const trainAgentBtn = document.getElementById("trainAgentBtn");
trainAgentBtn?.addEventListener("click", async () => {
    if (!currentAnalysis) return;
    const input = document.getElementById("agentCorrectionInput");
    const status = document.getElementById("agentTrainingStatus");
    trainAgentBtn.disabled = true;
    status.textContent = "학습 중...";
    try {
        const result = await trainVision({
            targetType: "agent",
            label: input.value,
            seconds: agentSampleTime(currentAnalysis),
            eventIndex: -1,
            predictedLabel: currentAnalysis.agent_prediction?.agent || ""
        });
        currentAnalysis.agent_prediction = {
            agent: result.label,
            confidence: 1,
            reason: "사용자가 실제 에이전트로 정정하여 학습 데이터에 반영했습니다.",
            learned_from_user_correction: true
        };
        currentAgentAbilities = await fetchAgentKit(result.label);
        currentAnalysis.agent_ability_catalog = currentAgentAbilities;
        renderAgent(currentAnalysis.agent_prediction);
        renderEvents(currentAnalysis.events || []);
        const samples = result.model?.sample_count ?? 0;
        const labels = result.model?.label_count ?? 0;
        status.textContent = `학습 완료 · 에이전트 샘플 ${samples}개 / 라벨 ${labels}종`;
        await saveAnalysisHistory(currentAnalysis, selectedFile?.name || "영상");
    } catch (error) {
        status.textContent = `오류: ${error.message}`;
    } finally {
        trainAgentBtn.disabled = false;
    }
});

saveAnalysisBtn?.addEventListener("click", async () => {
    if (!currentAnalysis) return;
    saveAnalysisBtn.disabled = true;
    saveAnalysisStatus.textContent = "저장 중...";
    try {
        const result = await saveAnalysisHistory(currentAnalysis, selectedFile?.name || "영상");
        if (result.ok) {
            saveAnalysisStatus.textContent = "내 분석에 저장되었습니다.";
        } else {
            saveAnalysisStatus.textContent = `저장 실패: ${result.detail}`;
        }
    } finally {
        saveAnalysisBtn.disabled = false;
    }
});

analyzeBtn.addEventListener("click", async () => {
    if (!selectedFile || analysisInProgress) return;

    setAnalysisInProgress(true);
    analyzeBtn.disabled = true;
    resetSaveControls();
    statusBox.textContent = "영상 업로드 및 AI 분석 중... 다른 메뉴는 창으로 열리며 분석은 계속됩니다.";

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

        statusBox.textContent = "Gemini 분석 완료 · VALORANT UI 기준과 학습 모델로 요원/스킬 판별을 교차 확인하는 중...";
        await applyLearnedVisionPredictions(data);
        renderResult(data);
        const historySave = await saveAnalysisHistory(data, selectedFile?.name || "영상");
        const baseStatus = data.usage && data.usage.premium
            ? `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS · PREMIUM`
            : `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS`;

        if (historySave.ok) {
            statusBox.textContent = `${baseStatus} · 기록 저장됨`;
            if (saveAnalysisStatus) saveAnalysisStatus.textContent = "자동 저장되었습니다.";
        } else {
            statusBox.textContent = `${baseStatus} · 자동 저장 실패`;
            if (saveAnalysisStatus) {
                saveAnalysisStatus.textContent = `자동 저장 실패: ${historySave.detail} · 저장 버튼으로 다시 시도할 수 있습니다.`;
            }
        }
    } catch (e) {
        statusBox.textContent = `오류: ${e.message}`;
    } finally {
        setAnalysisInProgress(false);
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
        msg.textContent = "피드백이 저장되었습니다. 다음 분석부터 집계 보정에 반영됩니다.";
    } catch (e) {
        msg.textContent = `오류: ${e.message}`;
    }
});

populateAgentOptions();
