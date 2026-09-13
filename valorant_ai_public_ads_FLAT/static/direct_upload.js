(() => {
    if (window.__valorantDirectGeminiUploadInstalled) return;
    window.__valorantDirectGeminiUploadInstalled = true;

    if (typeof analyzeBtn === "undefined" || !analyzeBtn) return;

    const LEGACY_FALLBACK_MAX_BYTES = 15 * 1024 * 1024;

    class DirectUploadError extends Error {
        constructor(message, options = {}) {
            super(message);
            this.name = "DirectUploadError";
            this.code = options.code || "";
            this.allowFallback = Boolean(options.allowFallback);
        }
    }

    function detailInfo(data, fallback = "요청에 실패했습니다.") {
        const detail = data?.detail;
        if (detail && typeof detail === "object") {
            return {
                code: String(detail.code || ""),
                message: String(detail.message || detail.code || fallback)
            };
        }
        return {
            code: "",
            message: String(detail || fallback)
        };
    }

    async function startDirectUpload(file) {
        const response = await fetch("/analysis/direct-upload/start", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                filename: file.name || "valorant-clip.mp4",
                size_bytes: file.size,
                mime_type: file.type || ""
            })
        });
        const data = await readJsonResponse(response, "직접 업로드 준비 서버");
        if (!response.ok) {
            const info = detailInfo(data, `직접 업로드 준비 실패 (HTTP ${response.status})`);
            const allowFallback = response.status === 404 || info.code === "DIRECT_UPLOAD_UNAVAILABLE";
            throw new DirectUploadError(info.message, {
                code: info.code,
                allowFallback
            });
        }
        if (!data.upload_url || !data.ticket_id) {
            throw new DirectUploadError("직접 업로드 URL을 받지 못했습니다.", {allowFallback: true});
        }
        return data;
    }

    async function uploadFileToGemini(file, session) {
        statusBox.textContent = "영상을 Gemini로 직접 업로드 중... Render 대역폭을 사용하지 않는 절약 모드입니다.";

        let response;
        try {
            response = await fetch(session.upload_url, {
                method: "POST",
                mode: "cors",
                headers: {
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                    "Content-Type": session.mime_type || file.type || "video/mp4"
                },
                body: file
            });
        } catch (error) {
            throw new DirectUploadError(
                `브라우저에서 Gemini로 직접 업로드하지 못했습니다: ${error?.message || error}`,
                {allowFallback: true}
            );
        }

        if (!response.ok) {
            let preview = "";
            try {
                preview = (await response.text()).trim().replace(/\s+/g, " ").slice(0, 180);
            } catch (_) {
                // Ignore response preview failures.
            }
            throw new DirectUploadError(
                preview
                    ? `Gemini 직접 업로드 실패 (HTTP ${response.status}): ${preview}`
                    : `Gemini 직접 업로드 실패 (HTTP ${response.status})`,
                {allowFallback: true}
            );
        }
    }

    async function analyzeUploadedGeminiFile(ticketId) {
        statusBox.textContent = "직접 업로드 완료 · Gemini AI가 영상을 분석하는 중...";
        const response = await fetch("/analysis/direct", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ticket_id: ticketId})
        });
        const data = await readJsonResponse(response, "직접 분석 서버");
        if (!response.ok) {
            const info = detailInfo(data, `분석 실패 (HTTP ${response.status})`);
            throw new DirectUploadError(info.message, {code: info.code, allowFallback: false});
        }
        return data;
    }

    async function analyzeViaDirectUpload(file) {
        const session = await startDirectUpload(file);
        await uploadFileToGemini(file, session);
        return await analyzeUploadedGeminiFile(session.ticket_id);
    }

    async function analyzeViaRenderFallback(file) {
        statusBox.textContent = "직접 업로드가 지원되지 않아 소용량 호환 모드로 분석 중...";
        const form = new FormData();
        form.append("file", file);
        const response = await fetch("/analyze", {
            method: "POST",
            credentials: "same-origin",
            body: form
        });
        const data = await readJsonResponse(response, "분석 서버");
        if (!response.ok) {
            const info = detailInfo(data, `분석 실패 (HTTP ${response.status})`);
            throw new DirectUploadError(info.message, {code: info.code});
        }
        data.transport_mode = data.transport_mode || "render_proxy_fallback";
        data.render_video_proxy_used = true;
        return data;
    }

    async function finishAnalysis(data, file) {
        statusBox.textContent = "Gemini 분석 완료 · VALORANT UI 기준과 학습 모델로 요원/스킬 판별을 교차 확인하는 중...";
        await applyLearnedVisionPredictions(data);
        renderResult(data);

        const historySave = await saveAnalysisHistory(data, file?.name || "영상");
        const directLabel = data.transport_mode === "browser_to_gemini_direct"
            ? " · 직접 업로드"
            : " · 호환 모드";
        const baseStatus = data.usage && data.usage.premium
            ? `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS${directLabel} · PREMIUM`
            : `분석 완료 · ${data.model_used || "Gemini"} · ${data.analysis_fps || 1} FPS${directLabel}`;

        if (historySave.ok) {
            statusBox.textContent = `${baseStatus} · 기록 저장됨`;
            if (saveAnalysisStatus) saveAnalysisStatus.textContent = "자동 저장되었습니다.";
        } else {
            statusBox.textContent = `${baseStatus} · 자동 저장 실패`;
            if (saveAnalysisStatus) {
                saveAnalysisStatus.textContent = `자동 저장 실패: ${historySave.detail} · 저장 버튼으로 다시 시도할 수 있습니다.`;
            }
        }
    }

    analyzeBtn.addEventListener("click", async (event) => {
        // Capture-phase handler prevents the legacy app.js click handler from
        // uploading the full video through Render.
        event.preventDefault();
        event.stopImmediatePropagation();

        if (!selectedFile || analysisInProgress) return;

        const file = selectedFile;
        setAnalysisInProgress(true);
        analyzeBtn.disabled = true;
        resetSaveControls();
        statusBox.textContent = "Render 대역폭 절약 모드 준비 중...";

        try {
            let data;
            try {
                data = await analyzeViaDirectUpload(file);
            } catch (error) {
                if (!(error instanceof DirectUploadError)) throw error;

                if (error.code === "LOGIN_REQUIRED") {
                    statusBox.innerHTML = '로그인이 필요합니다. <a href="/login">로그인하기</a>';
                    return;
                }

                if (!error.allowFallback) throw error;

                if (file.size > LEGACY_FALLBACK_MAX_BYTES) {
                    throw new DirectUploadError(
                        `${error.message} 브라우저 직접 업로드가 실패해 Render 호환 모드로 전환할 수 있지만, ` +
                        `${formatFileSize(LEGACY_FALLBACK_MAX_BYTES)} 초과 영상은 Render 대역폭 보호를 위해 호환 업로드를 막았습니다.`
                    );
                }

                console.warn("Gemini 직접 업로드 실패 · Render 소용량 fallback 사용", error);
                data = await analyzeViaRenderFallback(file);
            }

            await finishAnalysis(data, file);
        } catch (error) {
            statusBox.textContent = `오류: ${error?.message || error}`;
        } finally {
            setAnalysisInProgress(false);
            await refreshUsage();
        }
    }, {capture: true});
})();
