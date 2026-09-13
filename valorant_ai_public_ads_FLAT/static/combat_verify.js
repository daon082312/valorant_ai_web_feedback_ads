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
                reject(new Error("HUD 검증 프레임 이동 시간이 초과되었습니다."));
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

    function snapshotFullCanvas(maxWidth = 1100) {
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

    function snapshotBottomHudCanvas(targetWidth = 1280) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;
        // Keep the complete bottom-centre ability bar, including charges/cooldowns.
        const sx = Math.floor(sourceWidth * 0.14);
        const sy = Math.floor(sourceHeight * 0.66);
        const sw = Math.max(1, Math.floor(sourceWidth * 0.72));
        const sh = Math.max(1, sourceHeight - sy);
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

    function snapshotKillfeedCanvas(mode = "full-stack", targetWidth = 1500) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;
        let sx;
        let sy;
        let sw;
        let sh;

        if (mode === "lower-stack") {
            // Older rows move downward when simultaneous kills are added above.
            sx = Math.floor(sourceWidth * 0.27);
            sy = Math.floor(sourceHeight * 0.08);
            sw = Math.max(1, Math.floor(sourceWidth * 0.73));
            sh = Math.max(1, Math.floor(sourceHeight * 0.76));
        } else {
            // Full vertical stack: deliberately much taller than the normal feed.
            sx = Math.floor(sourceWidth * 0.27);
            sy = 0;
            sw = Math.max(1, Math.floor(sourceWidth * 0.73));
            sh = Math.max(1, Math.floor(sourceHeight * 0.80));
        }

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

    function canvasToJpeg(canvas, quality = 0.9) {
        return canvas.toDataURL("image/jpeg", quality);
    }

    function drawContained(ctx, source, x, y, width, height) {
        const scale = Math.min(width / source.width, height / source.height);
        const drawWidth = source.width * scale;
        const drawHeight = source.height * scale;
        const dx = x + (width - drawWidth) / 2;
        const dy = y + (height - drawHeight) / 2;
        ctx.drawImage(source, dx, dy, drawWidth, drawHeight);
    }

    function buildFullHudContextSheet(samples) {
        // Every temporal cell shows BOTH the whole fight and a large bottom-HUD
        // crop. This lets Gemini compare official ability icons across time.
        const columns = 2;
        const rows = Math.ceil(samples.length / columns);
        const cellWidth = 520;
        const fullHeight = 292;
        const hudHeight = 176;
        const labelHeight = 30;
        const gap = 10;
        const cellHeight = fullHeight + hudHeight + labelHeight + 8;
        const sheet = document.createElement("canvas");
        sheet.width = columns * cellWidth + (columns + 1) * gap;
        sheet.height = rows * cellHeight + (rows + 1) * gap;
        const ctx = sheet.getContext("2d", {alpha: false});
        ctx.fillStyle = "#07090d";
        ctx.fillRect(0, 0, sheet.width, sheet.height);
        ctx.font = "bold 16px sans-serif";
        ctx.textBaseline = "middle";

        samples.forEach((sample, index) => {
            const col = index % columns;
            const row = Math.floor(index / columns);
            const x = gap + col * (cellWidth + gap);
            const y = gap + row * (cellHeight + gap);

            ctx.fillStyle = "#111722";
            ctx.fillRect(x, y, cellWidth, fullHeight);
            drawContained(ctx, sample.full, x, y, cellWidth, fullHeight);

            const hudY = y + fullHeight + 4;
            ctx.fillStyle = "#0c1119";
            ctx.fillRect(x, hudY, cellWidth, hudHeight);
            drawContained(ctx, sample.hud, x, hudY, cellWidth, hudHeight);

            ctx.fillStyle = "#ffffff";
            const sign = sample.offset >= 0 ? "+" : "";
            ctx.fillText(
                `FULL + ENLARGED ABILITY HUD ${sign}${sample.offset.toFixed(2)}s`,
                x + 8,
                hudY + hudHeight + labelHeight / 2
            );
        });

        return sheet.toDataURL("image/jpeg", 0.86);
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
                // Dense enough to show HUD state before/after a cast and to keep
                // killfeed rows visible after simultaneous kills shift them down.
                const offsets = [-0.55, 0.10, 0.55, 1.15, 1.80];
                const samples = [];

                for (const offset of offsets) {
                    await seekTo(center + offset);
                    samples.push({
                        offset,
                        full: snapshotFullCanvas(1100),
                        hud: snapshotBottomHudCanvas(1280),
                        killfeedFull: snapshotKillfeedCanvas("full-stack", 1500),
                        killfeedLower: snapshotKillfeedCanvas("lower-stack", 1500)
                    });
                }

                // A catches the fresh top entry. B is later and deliberately
                // lower/taller, catching the same row after other kills push it.
                const killfeedA = canvasToJpeg(samples[1].killfeedFull, 0.92);
                const killfeedB = canvasToJpeg(samples[3].killfeedLower, 0.92);
                const fullHudSheet = buildFullHudContextSheet(samples);

                payloadEvents.push({
                    event_index: index,
                    timestamp: String(event.timestamp || ""),
                    observation: String(event.observation || ""),
                    feedback: String(event.feedback || ""),
                    frames: [killfeedA, killfeedB, fullHudSheet]
                });
            }
        } finally {
            try {
                await seekTo(originalTime);
            } catch (_) {
                // Preview restoration is optional.
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

        const body = await readJsonResponse(response, "HUD 검증 서버");
        if (!response.ok) {
            throw new Error(body.detail || `HUD 검증 실패 (HTTP ${response.status})`);
        }
        return body;
    }

    function applyUnifiedVerification(data, verification) {
        const events = Array.isArray(data?.events) ? data.events : [];
        const items = Array.isArray(verification?.events) ? verification.events : [];

        // The dedicated high-resolution HUD verifier has priority over the
        // low-resolution whole-video agent guess when it has useful evidence.
        const verifiedAgent = String(verification?.agent || "").trim();
        const agentConfidence = Math.max(0, Math.min(1, Number(verification?.agent_confidence || 0)));
        if (verification?.unified_hud_verification && verification?.portrait_reference_used) {
            data.agent_prediction_before_hud_verification = data.agent_prediction || null;
            if (verifiedAgent && verifiedAgent !== "Unknown" && agentConfidence >= 0.55) {
                data.agent_prediction = {
                    agent: verifiedAgent,
                    confidence: agentConfidence,
                    reason: String(verification.agent_evidence || "공식 HUD 아이콘 참조표와 확대 능력 HUD로 재검증했습니다."),
                    hud_verified: true,
                    original_agent: data.agent_prediction_before_hud_verification?.agent || "Unknown"
                };
            } else if (verifiedAgent === "Unknown") {
                data.agent_prediction = {
                    agent: "Unknown",
                    confidence: agentConfidence,
                    reason: String(verification.agent_evidence || "확대 HUD에서도 요원을 확정할 근거가 부족했습니다."),
                    hud_verified: true,
                    original_agent: data.agent_prediction_before_hud_verification?.agent || "Unknown"
                };
            }
        }

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

            const abilityConfidence = Math.max(0, Math.min(1, Number(item.ability_confidence || 0)));
            const verifiedAbility = String(item.ability_name || "").trim();
            event.hud_ability_verified = true;
            event.hud_ability_confidence = abilityConfidence;
            event.hud_ability_evidence = String(item.ability_evidence || "");
            if (verifiedAbility && abilityConfidence >= 0.58) {
                event.original_ability_name_before_hud_verification = event.ability_name || null;
                event.ability_name = verifiedAbility;
                event.ability_confidence = abilityConfidence;
            } else if (!verifiedAbility && abilityConfidence <= 0.35) {
                // Prefer 'unknown' over preserving a low-confidence hallucinated skill.
                if (Number(event.ability_confidence || 0) < 0.75) {
                    event.original_ability_name_before_hud_verification = event.ability_name || null;
                    event.ability_name = null;
                    event.ability_confidence = 0;
                }
            }

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
        data.combat_portrait_reference_used = Boolean(verification?.portrait_reference_used);
        data.hud_verification_used = Boolean(verification?.unified_hud_verification);
        data.hud_verification_token_usage = verification?.token_usage || {};
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
                ? (event.killfeed_supports_pov_kill ? "킬로그 · 본인 처치 확인" : "킬로그 · 다른/불명확 행 확인")
                : "킬로그 · 확인 안 됨";

            const portraitBadge = document.createElement("span");
            portraitBadge.className = "badge";
            portraitBadge.textContent = data.combat_portrait_reference_used
                ? "실제 Killfeed 초상화 · 대조"
                : "UI 참조표 · 생략";

            row.append(badge, killfeedBadge, portraitBadge);

            if (event.hud_ability_verified) {
                const abilityBadge = document.createElement("span");
                abilityBadge.className = "badge";
                const ac = Math.round(Number(event.hud_ability_confidence || 0) * 100);
                abilityBadge.textContent = event.ability_name
                    ? `스킬 검증 · ${event.ability_name} · ${ac}%`
                    : `스킬 검증 · 불확실 · ${ac}%`;
                row.appendChild(abilityBadge);
            }

            const evidence = document.createElement("span");
            evidence.className = "muted";
            evidence.textContent = event.killfeed_note || event.hud_ability_evidence || event.combat_evidence || "";
            row.appendChild(evidence);

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
                statusBoxEl.textContent = "공식 요원·스킬 UI와 전체 킬로그를 고해상도로 재검증하는 중...";
            }
            const verification = await requestCombatVerification(data);
            if (!verification?.verified) {
                data.combat_verification_used = false;
                return;
            }

            applyUnifiedVerification(data, verification);

            if (typeof fetchAgentKit === "function") {
                try {
                    currentAgentAbilities = await fetchAgentKit(data.agent_prediction?.agent || "");
                    data.agent_ability_catalog = currentAgentAbilities;
                } catch (_) {
                    // UI rendering can continue even if catalog refresh fails.
                }
            }

            originalRenderResult(data);
            decorateCombatRows(data);

            if (typeof saveAnalysisHistory === "function") {
                await saveAnalysisHistory(data, selectedFile?.name || "영상");
            }

            if (statusBoxEl) {
                const base = `분석 완료 · ${data.model_used || "Gemini"} · 요원/스킬/킬 HUD 재검증 완료`;
                statusBoxEl.textContent = data.usage?.premium ? `${base} · PREMIUM` : base;
            }
        } catch (error) {
            console.warn("HUD 통합 재검증 생략", error);
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
