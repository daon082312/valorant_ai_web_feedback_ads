(() => {
    const titleByPath = {
        "/history": "내 분석",
        "/guide": "사용 가이드",
        "/about": "About",
        "/premium": "Premium",
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

    function ensureModal() {
        if (modal) return;

        modal = document.createElement("div");
        modal.className = "analysis-page-modal";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-label", "사이트 페이지 창");

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
        close.addEventListener("click", closeModal);

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
        ensureModal();
        const parsed = embeddedUrl(url);
        modalTitle.textContent = titleByPath[parsed.pathname] || "페이지";
        modalSub.textContent = isAnalysisRunning()
            ? "영상 분석은 뒤에서 계속 진행됩니다."
            : "창을 닫으면 원래 화면으로 돌아갑니다.";
        frame.src = parsed.href;
        modal.classList.add("open");
        document.documentElement.classList.add("analysis-modal-open");
        document.body.classList.add("analysis-modal-open");
    }

    function closeModal() {
        if (!modal) return;
        modal.classList.remove("open");
        document.documentElement.classList.remove("analysis-modal-open");
        document.body.classList.remove("analysis-modal-open");
        if (frame) frame.src = "about:blank";
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && modal?.classList.contains("open")) {
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
            || url.pathname === "/guide"
            || url.pathname === "/about";
        const analysisModal = isAnalysisRunning() && Object.hasOwn(titleByPath, url.pathname);

        if (!alwaysModal && !analysisModal) return;

        event.preventDefault();
        event.stopImmediatePropagation();
        openModal(url.href);
    }, true);

    const statusObserver = new MutationObserver(() => {
        const status = document.getElementById("status");
        if (!status) return;
        status.textContent = status.textContent
            .replace("다른 메뉴는 새 탭으로 열리며", "다른 메뉴는 창으로 열리며")
            .replace("다른 메뉴는 창으로 열리며 분석은 계속됩니다.", "다른 메뉴는 창으로 열리며 분석은 계속됩니다.");
    });

    statusObserver.observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true
    });
})();
