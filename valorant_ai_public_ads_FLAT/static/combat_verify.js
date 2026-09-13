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

    function snapshotRoundRoleCanvas(targetWidth = 1280) {
        const sourceWidth = player.videoWidth || 1280;
        const sourceHeight = player.videoHeight || 720;
        const sx = Math.floor(sourceWidth * 0.22);
        const sy = 0;
        const sw = Math.max(1, Math.floor(sourceWidth * 0.56));
        const sh = Math.max(1, Math.floor(sourceHeight * 0.24));
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
            sx = Math.floor(sourceWidth * 0.27);
            sy = Math.floor(sourceHeight * 0.08);
            sw = Math.max(1, Math.floor(sourceWidth * 0.73));
            sh = Math.max(1, Math.floor(sourceHeight * 0.76));
        } else {
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

    function cheatCandidateScore(event) {
        const text = `${event?.observation || ""} ${event?.feedback || ""}`.toLowerCase();
        let score = 0;
        if (String(event?.category || "") === "aim") score += 4;
        if (["high", "medium"].includes(String(event?.severity || ""))) score += 1;
        if (/(킬|처치|헤드|에임|교전|사격|aim|kill|head|flick|track|spray|duel)/i.test(text)) score += 3;
        if (Number(event?.confidence || 0) >= 0.7) score += 1;
        return score;
    }

    function selectDenseAimEventIndices(events) {
        const ranked = events.map((event, index) => ({index, score: cheatCandidateScore(event)}));
        ranked.sort((a, b) => b.score - a.score || a.index - b.index);
        const chosen = ranked.filter(item => item.score > 0).slice(0, 3).map(item => item.index);
        const minimum = Math.min(2, events.length);
        for (const item of ranked) {
            if (chosen.length >= minimum) break;
            if (!chosen.includes(item.index)) chosen.push(item.index);
        }
        return new Set(chosen.slice(0, 3));
    }

    async function captureDenseAimSamples(center) {
        const offsets = [-0.42, -0.30, -0.18, -0.06, 0.06, 0.18, 0.30, 0.42];
        const samples = [];
        for (const offset of offsets) {
            await seekTo(center + offset);
            samples.push({
                offset,
                full: snapshotFullCanvas(900)
            });
        }
        return samples;
    }

    function buildFullHudContextSheet(samples, aimSamples = []) {
        const columns = 2;
        const rows = Math.ceil(samples.length / columns);
        const cellWidth = 520;
        const fullHeight = 260;
        const roleHeight = 132;
        const hudHeight = 158;
        const labelHeight = 30;
        const gap = 10;
        const cellHeight = fullHeight + roleHeight + hudHeight + labelHeight + 12;

        const aimColumns = 4;
        const aimRows = aimSamples.length ? Math.ceil(aimSamples.length / aimColumns) : 0;
        const aimCellWidth = 250;
        const aimImageHeight = 141;
        const aimLabelHeight = 24;
        const aimHeaderHeight = aimSamples.length ? 34 : 0;
        const aimSectionHeight = aimSamples.length
            ? aimHeaderHeight + aimRows * (aimImageHeight + aimLabelHeight + gap) + gap
            : 0;

        const sheet = document.createElement("canvas");
        sheet.width = columns * cellWidth + (columns + 1) * gap;
        sheet.height = rows * cellHeight + (rows + 1) * gap + aimSectionHeight;
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

            const roleY = y + fullHeight + 4;
            ctx.fillStyle = "#0b1018";
            ctx.fillRect(x, roleY, cellWidth, roleHeight);
            drawContained(ctx, sample.role, x, roleY, cellWidth, roleHeight);

            const hudY = roleY + roleHeight + 4;
            ctx.fillStyle = "#0c1119";
            ctx.fillRect(x, hudY, cellWidth, hudHeight);
            drawContained(ctx, sample.hud, x, hudY, cellWidth, hudHeight);

            ctx.fillStyle = "#ffffff";
            const sign = sample.offset >= 0 ? "+" : "";
            ctx.fillText(
                `FULL + ROUND ROLE HUD + ABILITY HUD ${sign}${sample.offset.toFixed(2)}s`,
                x + 8,
                hudY + hudHeight + labelHeight / 2
            );
        });

        if (aimSamples.length) {
            const sectionY = rows * cellHeight + (rows + 1) * gap;
            ctx.fillStyle = "#151b26";
            ctx.fillRect(gap, sectionY, sheet.width - gap * 2, aimHeaderHeight - 4);
            ctx.fillStyle = "#ffffff";
            ctx.font = "bold 17px sans-serif";
            ctx.fillText(
                "AIM MOTION STRIP · chronological left→right, top→bottom · dense frames",
                gap + 10,
                sectionY + (aimHeaderHeight - 4) / 2
            );
            ctx.font = "bold 13px sans-serif";

            aimSamples.forEach((sample, index) => {
                const col = index % aimColumns;
                const row = Math.floor(index / aimColumns);
                const x = gap + col * (aimCellWidth + gap);
                const y = sectionY + aimHeaderHeight + row * (aimImageHeight + aimLabelHeight + gap);
                ctx.fillStyle = "#0d121b";
                ctx.fillRect(x, y, aimCellWidth, aimImageHeight);
                drawContained(ctx, sample.full, x, y, aimCellWidth, aimImageHeight);
                ctx.fillStyle = "#ffffff";
                const sign = sample.offset >= 0 ? "+" : "";
                ctx.fillText(`${sign}${sample.offset.toFixed(2)}s`, x + 6, y + aimImageHeight + aimLabelHeight / 2);
            });
        }

        return sheet.toDataURL("image/jpeg", aimSamples.length ? 0.84 : 0.87);
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
        const denseAimIndices = selectDenseAimEventIndices(events);
        try {
            for (let index = 0; index < Math.min(events.length, 6); index += 1) {
                const event = events[index];
                const center = timestampToSeconds(event.timestamp);
                const offsets = [-0.55, 0.10, 0.55, 1.15, 1.80];
                const samples = [];

                for (const offset of offsets) {
                    await seekTo(center + offset);
                    samples.push({
                        offset,
                        full: snapshotFullCanvas(1100),
                        role: snapshotRoundRoleCanvas(1280),
                        hud: snapshotBottomHudCanvas(1280),
                        killfeedFull: snapshotKillfeedCanvas("full-stack", 1500),
                        killfeedLower: snapshotKillfeedCanvas("lower-stack", 1500)
                    });
                }

                let aimSamples = [];
                if (denseAimIndices.has(index)) {
                    aimSamples = await captureDenseAimSamples(center);
                }

                const killfeedA = canvasToJpeg(samples[1].killfeedFull, 0.92);
                const killfeedB = canvasToJpeg(samples[3].killfeedLower, 0.92);
                const fullHudSheet = buildFullHudContextSheet(samples, aimSamples);

                payloadEvents.push({
                    event_index: index,
                    timestamp: String(event.timestamp || ""),
                    observation: String(event.observation || ""),
                    feedback: String(event.feedback || ""),
                    dense_motion_strip: aimSamples.length > 0,
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

            const side = ["attacker", "defender"].includes(String(item.side || ""))
                ? String(item.side)
                : "unknown";
            const sideConfidence = Math.max(0, Math.min(1, Number(item.side_confidence || 0)));
            event.side = side;
            event.side_confidence = sideConfidence;
            event.side_evidence = String(item.side_evidence || "");
            event.side_verified = true;

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
                if (Number(event.ability_confidence || 0) < 0.75) {
                    event.original_ability_name_before_hud_verification = event.ability_name || null;
                    event.ability_name = null;
                    event.ability_confidence = 0;
                }
            }

            const correctionConfidence = Math.max(confidence, sideConfidence, abilityConfidence);
            if (item.replace_text && correctionConfidence >= 0.62) {
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
        data.side_verification_used = Boolean(verification?.side_verification);
        data.cheat_verification_used = Boolean(verification?.cheat_suspicion_verification);
        data.cheat_assessment = verification?.cheat_assessment || null;
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
        const sideLabels = {
            attacker: "공격팀",
            defender: "수비팀",
            unknown: "진영 불확실"
        };

        boxes.forEach((box, index) => {
            const event = events[index];
            if (!event?.combat_verified) return;

            const existing = box.querySelector(".combat-verification-row");
            if (existing) existing.remove();

            const row = document.createElement("div");
            row.className = "vision-ability-row combat-verification-row";

            if (event.side_verified) {
                const sideBadge = document.createElement("span");
                sideBadge.className = "badge";
                const sc = Math.round(Number(event.side_confidence || 0) * 100);
                sideBadge.textContent = `진영 · ${sideLabels[event.side] || "진영 불확실"} · ${sc}%`;
                row.appendChild(sideBadge);
            }

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
            evidence.textContent = [
                event.side_evidence,
                event.killfeed_note,
                event.hud_ability_evidence,
                event.combat_evidence
            ].filter(Boolean).join(" · ");
            row.appendChild(evidence);

            const head = box.querySelector(".event-head");
            if (head?.nextSibling) {
                box.insertBefore(row, head.nextSibling);
            } else {
                box.appendChild(row);
            }
        });
    }

    function renderCheatAssessment(data) {
        const badge = document.getElementById("cheatAssessmentBadge");
        const score = document.getElementById("cheatAssessmentScore");
        const summary = document.getElementById("cheatAssessmentSummary");
        const indicators = document.getElementById("cheatIndicators");
        const evidence = document.getElementById("cheatEvidence");
        const alternatives = document.getElementById("cheatAlternatives");
        const disclaimer = document.getElementById("cheatDisclaimer");
        if (!badge || !score || !summary || !indicators || !evidence || !alternatives || !disclaimer) return;

        const assessment = data?.cheat_assessment;
        if (!assessment) {
            badge.textContent = "검증 대기";
            score.textContent = "연속 에임 프레임을 아직 검증하지 않았습니다.";
            summary.textContent = "";
            indicators.innerHTML = "";
            evidence.innerHTML = "";
            alternatives.innerHTML = "";
            disclaimer.textContent = "영상만으로 치트 사용을 확정할 수 없습니다.";
            return;
        }

        const ratingLabels = {
            no_clear_evidence: "뚜렷한 이상 근거 없음",
            insufficient_evidence: "판정 근거 부족",
            suspicious: "의심 패턴 있음",
            strongly_suspicious: "강한 의심 패턴"
        };
        const indicatorLabels = {
            aim_snap: "반복적인 비정상 에임 스냅",
            wall_tracking: "비가시 표적 추적 의심",
            information_anomaly: "정보 없이 반복되는 사전 대응",
            unnatural_target_switching: "비정상적으로 기계적인 타깃 전환",
            trigger_like_timing: "비정상적으로 일관된 발사 타이밍",
            none: "뚜렷한 이상 신호 없음"
        };

        const confidence = Math.round(Math.max(0, Math.min(1, Number(assessment.confidence || 0))) * 100);
        badge.textContent = `${ratingLabels[assessment.rating] || assessment.rating} · 신뢰도 ${confidence}%`;
        score.textContent = `핵 의심도 점수 ${Number(assessment.suspicion_score || 0)} / 100 · 확률이 아닙니다.`;
        summary.textContent = String(assessment.summary || "");

        indicators.innerHTML = "";
        for (const item of Array.isArray(assessment.indicators) ? assessment.indicators : []) {
            const li = document.createElement("li");
            li.textContent = indicatorLabels[item] || String(item);
            indicators.appendChild(li);
        }

        evidence.innerHTML = "";
        for (const item of Array.isArray(assessment.evidence) ? assessment.evidence : []) {
            const li = document.createElement("li");
            li.textContent = String(item);
            evidence.appendChild(li);
        }
        if (!evidence.children.length) {
            const li = document.createElement("li");
            li.textContent = "반복되는 직접 이상 근거가 충분하지 않습니다.";
            evidence.appendChild(li);
        }

        alternatives.innerHTML = "";
        for (const item of Array.isArray(assessment.benign_explanations) ? assessment.benign_explanations : []) {
            const li = document.createElement("li");
            li.textContent = String(item);
            alternatives.appendChild(li);
        }
        disclaimer.textContent = String(assessment.disclaimer || "영상만으로 치트 사용을 확정할 수 없습니다.");
    }

    async function verifyAfterRender(data) {
        if (!data || data.combat_verification_attempted) return;
        data.combat_verification_attempted = true;

        try {
            if (statusBoxEl) {
                statusBoxEl.textContent = "공격/수비·요원·스킬·킬로그와 연속 에임 프레임을 고해상도로 재검증하는 중...";
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
            renderCheatAssessment(data);

            if (typeof saveAnalysisHistory === "function") {
                await saveAnalysisHistory(data, selectedFile?.name || "영상");
            }

            if (statusBoxEl) {
                const base = `분석 완료 · ${data.model_used || "Gemini"} · HUD/전투/핵 의심도 재검증 완료`;
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
        renderCheatAssessment(data);
        verifyAfterRender(data);
    };
})();