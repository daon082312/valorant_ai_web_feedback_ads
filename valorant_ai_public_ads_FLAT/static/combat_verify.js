(() => {
    const player = document.getElementById("videoPlayer");
    const statusBoxEl = document.getElementById("status");
    if (!player || typeof renderResult !== "function") return;

    const originalRenderResult = renderResult;

    function waitForSeek(target, timeoutMs = 4500) {
        return new Promise((resolve, reject) => {
            let timer = null;
            const done = () => {
                if (timer) clearTimeout(timer);
                target.removeEventListener("seeked", done);
                resolve();
            };
            target.addEventListener("seeked", done, {once: true});
            timer = setTimeout(() => {
                target.removeEventListener("seeked", done);
                reject(new Error("전투 검증 프레임 이동 시간이 초과되었습니다."));
            }, timeoutMs);
        });
    }

    async function seekTo(seconds) {
        const duration = Number(player.duration || 0);
        const target = Math.max(
            0,
            Math.min(Number(seconds || 0), duration > 0 ? Math.max(0, duration - 0.04) : Number(seconds || 0))
        );
        if (Math.abs(Number(player.currentTime || 0) - target) <= 0.035) return;
        player.currentTime = target;
        await waitForSeek(player);
    }

    function frameToDataUrl(maxWidth = 960, quality = 0.76) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;
        const width = Math.min(maxWidth, sourceWidth);
        const height = Math.max(1, Math.round(sourceHeight * (width / sourceWidth)));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d", {alpha: false});
        ctx.drawImage(player, 0, 0, width, height);
        return canvas.toDataURL("image/jpeg", quality);
    }

    async function captureVerificationFrames(events) {
        if (player.readyState < 1) {
            await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(new Error("영상 메타데이터를 읽지 못했습니다.")), 5000);
                player.addEventListener("loadedmetadata", () => {
                    clearTimeout(timer);
                    resolve();
                }, {once: true});
            });
        }

        const originalTime = Number(player.currentTime || 0);
        const wasPaused = player.paused;
        player.pause();

        const payloadEvents = [];
        try {
            for (let index = 0; index < Math.min(events.length, 6); index += 1) {
                const event = events[index];
                const center = timestampToSeconds(event.timestamp);
                const offsets = [-0.38, 0.08, 0.58];
                const frames = [];

                for (const offset of offsets) {
                    await seekTo(center + offset);
                    frames.push(frameToDataUrl(960, 0.76));
                }

                payloadEvents.push({
                    event_index: index,
                    timestamp: String(event.timestamp || ""),
                    observation: String(event.observation || ""),
                    feedback: String(event.feedback || ""),
                    frames
                });
            }
        } finally {
            try {
                await seekTo(originalTime);
            } catch (_) {
                // Restoring the preview position is optional.
            }
            if (!wasPaused) {
                player.play().catch(() => {});
            }
        }
        return payloadEvents;
    }

    async function requestCombatVerification(data) {
        const events = Array.isArray(data?.events) ? data.events.slice(0, 6) : [];
        if (!events.length || !data?.analysis_id || !player.src) {
            return {verified: false, events: []};
        }

        const payloadEvents = await captureVerificationFrames(events);
        const response = await fetch("/vision/combat-verify", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                analysis_id: data.analysis_id,
                events: payloadEvents
            })
        });

        const body = await readJsonResponse(response, "전투 결과 검증 서버");
        if (!response.ok) {
            throw new Error(body.detail || `전투 결과 검증 실패 (HTTP ${response.status})`);
        }
        return body;
    }

    function applyCombatVerification(data, verification) {
        const events = Array.isArray(data?.events) ? data.events : [];
        const items = Array.isArray(verification?.events) ? verification.events : [];

        for (const item of items) {
            const index = Number(item.event_index);
            const event = events[index];
            if (!event) continue;

            const confidence = Math.max(0, Math.min(1, Number(item.confidence || 0)));
            event.combat_outcome = item.outcome || "uncertain";
            event.combat_confidence = confidence;
            event.combat_evidence = String(item.evidence || "");
            event.combat_verified = true;

            // Replace the main model's wording only when the frame verifier is
            // reasonably confident that the original kill/death interpretation
            // was wrong or omitted a clearly visible result.
            if (item.replace_text && confidence >= 0.62) {
                const correctedObservation = String(item.corrected_observation || "").trim();
                const correctedFeedback = String(item.corrected_feedback || "").trim();
                if (correctedObservation) event.observation = correctedObservation;
                if (correctedFeedback) event.feedback = correctedFeedback;
                event.combat_text_corrected = true;
            }
        }

        data.combat_verification_used = Boolean(verification?.verified && items.length);
        data.combat_verification_model = verification?.model_used || "";
        return data;
    }

    function decorateCombatRows(data) {
        const events = Array.isArray(data?.events) ? data.events : [];
        const boxes = document.querySelectorAll("#events .event");
        const labels = {
            kill: "적 처치 확인",
            death: "본인 사망 확인",
            assist: "어시스트 확인",
            survived: "생존 확인",
            no_combat: "킬/데스 없음",
            uncertain: "전투 결과 불확실"
        };

        boxes.forEach((box, index) => {
            const event = events[index];
            if (!event?.combat_verified) return;

            const existing = box.querySelector(".combat-verification-row");
            if (existing) existing.remove();

            const row = document.createElement("div");
            row.className = "vision-ability-row combat-verification-row";

            const badge = document.createElement("span");
            badge.className = "badge";
            const confidence = Math.round(Number(event.combat_confidence || 0) * 100);
            badge.textContent = `전투 검증 · ${labels[event.combat_outcome] || event.combat_outcome} · ${confidence}%`;

            const evidence = document.createElement("span");
            evidence.className = "muted";
            evidence.textContent = event.combat_evidence || "";

            row.append(badge, evidence);
            const head = box.querySelector(".event-head");
            if (head?.nextSibling) {
                box.insertBefore(row, head.nextSibling);
            } else {
                box.appendChild(row);
            }
        });
    }

    async function verifyAfterRender(data) {
        if (!data || data.combat_verification_attempted) return;
        data.combat_verification_attempted = true;

        try {
            if (statusBoxEl) {
                statusBoxEl.textContent = "주요 장면의 킬/데스를 고해상도 프레임으로 재검증하는 중...";
            }
            const verification = await requestCombatVerification(data);
            if (!verification?.verified) {
                data.combat_verification_used = false;
                return;
            }

            applyCombatVerification(data, verification);
            originalRenderResult(data);
            decorateCombatRows(data);

            if (typeof saveAnalysisHistory === "function") {
                await saveAnalysisHistory(data, selectedFile?.name || "영상");
            }

            if (statusBoxEl) {
                const base = `분석 완료 · ${data.model_used || "Gemini"} · 킬/데스 프레임 검증 완료`;
                statusBoxEl.textContent = data.usage?.premium ? `${base} · PREMIUM` : base;
            }
        } catch (error) {
            console.warn("전투 결과 재검증 생략", error);
            data.combat_verification_used = false;
            data.combat_verification_error = String(error?.message || error || "verification_failed");
        }
    }

    renderResult = function patchedRenderResult(data) {
        originalRenderResult(data);
        decorateCombatRows(data);
        verifyAfterRender(data);
    };
})();
