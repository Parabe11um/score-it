document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-invitation-path]").forEach((control) => {
        const input = control.querySelector("input");
        const button = control.querySelector("[data-copy-invitation]");
        const status = control.querySelector("[data-copy-status]");
        const absoluteUrl = new URL(control.dataset.invitationPath, window.location.origin).href;

        input.value = absoluteUrl;

        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(absoluteUrl);
            } catch (error) {
                input.focus();
                input.select();
                document.execCommand("copy");
            }
            status.textContent = "Скопировано";
            window.setTimeout(() => {
                status.textContent = "";
            }, 1800);
        });
    });
});
