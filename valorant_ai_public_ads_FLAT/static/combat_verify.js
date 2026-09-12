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

    function snapshotFullCanvas(maxWidth = 960) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;
        const width = Math.min(maxWidth, sourceWidth);
        const height = Math.max(1, Math.round(sourceHeight * (width / sourceWidth)));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d", {alpha: false});
        ctx.drawImage(player, 0, 0, width, height);
        return canvas;
    }

    function snapshotKillfeedCanvas(targetWidth = 720) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;

        // VALORANT killfeed is in the upper-right. Keep a deliberately generous
        // crop so different HUD scales / resolutions still include all entries.
        const sx = Math.floor(sourceWidth * 0.52);
        const sy = 0;
        const sw = Math.max(1, Math.floor(sourceWidth * 0.48));
        const sh = Math.max(1, Math.floor(sourceHeight * 0.36));
        const width = targetWidth;
        const height = Math.max(1, Math.round(sh * (width / sw)));

        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d", {alpha: false});
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(player, sx, sy, sw, sh, 0, 0, width, height);
        return canvas;
    }

    function buildContactSheet(samples, type) {
        const isKillfeed = type === "killfeed";
        const columns = 3;
        const rows = Math.ceil(samples.length / columns);
        const cellWidth = isKillfeed ? 420 : 350;
        const imageHeight = isKillfeed ? 180 : 197;
        const labelHeight = 28;
        const gap = 8;
        const sheet = document.createElement("canvas");
        sheet.width = columns * cellWidth + (columns + 1) * gap;
        sheet.height = rows * (imageHeight + labelHeight) + (rows + 1) * gap;
        const ctx = sheet.getContext("2d", {alpha: false});
        ctx.fillStyle = "#07090d";
        ctx.fillRect(0, 0, sheet.width, sheet.height);
        ctx.font = "bold 16px sans-serif";
        ctx.textBaseline = "middle";

        samples.forEach((sample, index) => {
            const col = index % columns;
            const row = Math.floor(index / columns);
            const x = gap + col * (cellWidth + gap);
            const y = gap + row * (imageHeight + labelHeight + gap);
            const source = isKillfeed ? sample.killfeed : sample.full;

            ctx.fillStyle = "#111722";
            ctx.fillRect(x, y, cellWidth, imageHeight);

            const scale = Math.min(cellWidth / source.width, imageHeight / source.height);
            const drawWidth = source.width * scale;
            const drawHeight = source.height * scale;
            const dx = x + (cellWidth - drawWidth) / 2;
            const dy = y + (imageHeight - drawHeight) / 2;
            ctx.drawImage(source, dx, dy, drawWidth, drawHeight);

            ctx.fillStyle = "#ffffff";
            const sign = sample.offset >= 0 ? "+" : "";
            const prefix = isKillfeed ? "KILLFEED" : "FULL";
            ctx.fillText(`${prefix} ${sign}${sample.offset.toFixed(2)}s`, x + 8, y + imageHeight + labelHeight / 2);
        });

        return sheet.toDataURL("image/jpeg", isKillfeed ? 0.78 : 0.70);
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

                // Main timestamps originate from ~1 FPS video sampling, so scan
                // a wider window at much denser intervals. Killfeed entries often
                // remain visible for a short time after the actual kill.
                const offsets = [-0.35, 0.00, 0.30, 0.60, 0.95, 1.30];
                const samples = [];

                for (const offset of offsets) {
                    await seekTo(center + offset);
                    samples.push({
                        offset,
                        full: snapshotFullCanvas(960),
                        killfeed: snapshotKillfeedCanvas(720)
                    });
                }

                // Only two images are sent per event: a magnified killfeed
                // timeline and a full-scene timeline. This is cheaper than
                // sending every raw frame separately while making the small UI
                // dramatically easier for Gemini to read.
                const killfeedSheet = buildContactSheet(samples, "killfeed");
                const fullSheet = buildContactSheet(samples, "full");

                payloadEvents.push({
                    event_index: index,
                    timestamp: String(event.timestamp || ""),
                    observation: String(event.observation || ""),
                    feedback: String(event.feedback || ""),
                    frames: [killfeedSheet, fullSheet]
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
                summary: String(data.summary || ""),
                player_agent: String(data.agent_prediction?.agent || ""),
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
            event.killfeed_visible = Boolean(item.killfeed_visible);
            event.killfeed_supports_pov_kill = Boolean(item.killfeed_supports_pov_kill);
            event.killfeed_note = String(item.killfeed_note || "");

            if (item.replace_text && confidence >= 0.62) {
                const correctedObservation = String(item.corrected_observation || "").trim();
                const correctedFeedback = String(item.corrected_feedback || "").trim();
                if (correctedObservation) event.observation = correctedObservation;
                if (correctedFeedback) event.feedback = correctedFeedback;
                event.combat_text_corrected = true;
            }
        }

        if (verification?.replace_summary) {
            const correctedSummary = String(verification.corrected_summary || "").trim();
            if (correctedSummary) {
                data.original_summary_before_combat_verification = data.summary;
                data.summary = correctedSummary;
                data.combat_summary_corrected = true;
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

            const killfeedBadge = document.createElement("span");
            killfeedBadge.className = "badge";
            killfeedBadge.textContent = event.killfeed_visible
                ? (event.killfeed_supports_pov_kill ? "킬로그 · 본인 처치 확인" : "킬로그 · 확인됨")
                : "킬로그 · 확인 안 됨";

            const evidence = document.createElement("span");
            evidence.className = "muted";
            evidence.textContent = event.killfeed_note || event.combat_evidence || "";

            row.append(badge, killfeedBadge, evidence);
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
                statusBoxEl.textContent = "주요 장면의 우측 상단 킬로그를 확대해 킬/데스를 재검증하는 중...";
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
                const base = `분석 완료 · ${data.model_used || "Gemini"} · 킬로그 확대 검증 완료`;
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
