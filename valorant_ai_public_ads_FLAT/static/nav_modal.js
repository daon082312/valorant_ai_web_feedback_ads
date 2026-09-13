(() => {
    const titleByPath = {
        "/history": "내 분석",
        "/guide": "사용 가이드",
        "/about": "About",
        "/premium": "Premium",
        "/donate": "후원",
        "/privacy": "Privacy",
        "/terms": "Terms"
    };

    let modal = null;
    let frame = null;
    let modalTitle = null;
    let modalSub = null;

    function isAnalysisRunning() {
        return document.documentElement.classList.contains("analysis-in-progress");
    }

    function cleanupModalState() {
        document.documentElement.classList.remove("analysis-modal-open");
        document.body.classList.remove("analysis-modal-open");
        document.documentElement.style.overflow = "";
        document.body.style.overflow = "";
    }

    function closeModal() {
        cleanupModalState();

        if (frame) {
            try {
                frame.src = "about:blank";
            } catch (_) {
                // Ignore iframe cleanup errors.
            }
        }

        if (modal) {
            modal.classList.remove("open");
            modal.setAttribute("aria-hidden", "true");
            modal.style.pointerEvents = "none";
            modal.remove();
        }

        modal = null;
        frame = null;
        modalTitle = null;
        modalSub = null;
    }

    function ensureModal() {
        if (modal && document.body.contains(modal)) return;

        document.querySelectorAll(".analysis-page-modal").forEach(node => node.remove());
        cleanupModalState();

        modal = document.createElement("div");
        modal.className = "analysis-page-modal";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-label", "사이트 페이지 창");
        modal.setAttribute("aria-hidden", "true");

        const card = document.createElement("div");
        card.className = "analysis-page-modal-card";

        const head = document.createElement("div");
        head.className = "analysis-page-modal-head";

        const titleWrap = document.createElement("div");
        titleWrap.className = "analysis-page-modal-title";

        modalTitle = document.createElement("strong");
        modalTitle.textContent = "페이지";

        modalSub = document.createElement("span");
        modalSub.textContent = "창을 닫으면 원래 화면으로 돌아갑니다.";

        const close = document.createElement("button");
        close.type = "button";
        close.className = "analysis-page-modal-close";
        close.setAttribute("aria-label", "창 닫기");
        close.textContent = "×";
        close.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            closeModal();
        });

        frame = document.createElement("iframe");
        frame.className = "analysis-page-modal-frame";
        frame.title = "사이트 내부 페이지";

        titleWrap.append(modalTitle, modalSub);
        head.append(titleWrap, close);
        card.append(head, frame);
        modal.appendChild(card);
        document.body.appendChild(modal);

        modal.addEventListener("click", (event) => {
            if (event.target === modal) closeModal();
        });
    }

    function embeddedUrl(url) {
        const parsed = new URL(url, window.location.href);
        parsed.searchParams.set("embedded", "1");
        return parsed;
    }

    function openModal(url) {
        if (modal) closeModal();
        ensureModal();

        const parsed = embeddedUrl(url);
        modalTitle.textContent = titleByPath[parsed.pathname] || "페이지";
        modalSub.textContent = isAnalysisRunning()
            ? "영상 분석은 뒤에서 계속 진행됩니다."
            : "창을 닫으면 원래 화면으로 돌아갑니다.";

        frame.src = parsed.href;
        modal.setAttribute("aria-hidden", "false");
        modal.style.pointerEvents = "auto";
        modal.classList.add("open");
        document.documentElement.classList.add("analysis-modal-open");
        document.body.classList.add("analysis-modal-open");
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && modal?.classList.contains("open")) {
            event.preventDefault();
            closeModal();
        }
    });

    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link) return;

        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) return;
        if (url.hash && url.pathname === window.location.pathname) return;

        const alwaysModal = link.dataset.modalPage === "always"
            || url.pathname === "/history"
            || url.pathname === "/premium"
            || url.pathname === "/donate"
            || url.pathname === "/guide"
            || url.pathname === "/about";
        const analysisModal = isAnalysisRunning() && Object.hasOwn(titleByPath, url.pathname);

        if (!alwaysModal && !analysisModal) return;

        event.preventDefault();
        event.stopImmediatePropagation();
        openModal(url.href);
    }, true);

    window.addEventListener("pageshow", () => {
        document.querySelectorAll(".analysis-page-modal").forEach(node => node.remove());
        modal = null;
        frame = null;
        modalTitle = null;
        modalSub = null;
        cleanupModalState();
    });
})();
