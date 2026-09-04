(() => {
    const root = document.getElementById("organizer-state");
    const copyButton = document.getElementById("copy-link");
    const publicUrl = document.getElementById("public-url");

    if (copyButton && publicUrl) {
        copyButton.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(publicUrl.value);
                copyButton.textContent = "Скопировано";
            } catch (_error) {
                publicUrl.select();
                document.execCommand("copy");
                copyButton.textContent = "Скопировано";
            }
            window.setTimeout(() => {
                copyButton.textContent = "Копировать";
            }, 1800);
        });
    }

    if (!root) return;

    const participantList = document.getElementById("participant-list");
    const participantPill = document.getElementById("participant-pill");
    const participantProgressSummary = document.getElementById("participant-progress-summary");
    const votedCount = document.getElementById("voted-count");
    const participantCount = document.getElementById("participant-count");
    const minimumCount = document.getElementById("minimum-count");
    const thresholdHint = document.getElementById("threshold-hint");
    const revealButton = document.getElementById("reveal-button");
    const revealedVotes = document.getElementById("revealed-votes");
    const averageValue = document.getElementById("average-value");
    const csrfToken = document.querySelector("#organizer-csrf [name=csrfmiddlewaretoken]")?.value;

    async function copyText(value) {
        try {
            await navigator.clipboard.writeText(value);
        } catch (_error) {
            const input = document.createElement("textarea");
            input.value = value;
            input.setAttribute("readonly", "");
            input.style.position = "fixed";
            input.style.opacity = "0";
            document.body.append(input);
            input.select();
            document.execCommand("copy");
            input.remove();
        }
    }

    function initial(name) {
        return Array.from(name.trim())[0]?.toUpperCase() || "?";
    }

    function renderParticipants(participants) {
        if (!participantList) return;
        participantList.replaceChildren();
        if (!participants.length) {
            const empty = document.createElement("div");
            empty.className = "compact-empty";
            empty.textContent = "Ждём участников по ссылке.";
            participantList.append(empty);
            return;
        }
        participants.forEach((participant) => {
            const row = document.createElement("div");
            row.className = "participant";

            const avatar = document.createElement("span");
            avatar.className = "avatar";
            avatar.textContent = initial(participant.name);

            const name = document.createElement("strong");
            name.textContent = participant.name;

            const status = document.createElement("span");
            status.className = `participant__state participant__state--${participant.progress_status}`;
            status.textContent = participant.progress_label;

            const main = document.createElement("div");
            main.className = "participant__main";
            main.append(name, status);

            const copyButton = document.createElement("button");
            copyButton.className = "participant__action";
            copyButton.type = "button";
            copyButton.dataset.resumeCopy = participant.resume_url;
            copyButton.textContent = "Скопировать ссылку";
            copyButton.setAttribute(
                "aria-label",
                `Скопировать персональную ссылку участника ${participant.name}`,
            );

            const rotateButton = document.createElement("button");
            rotateButton.className = "participant__action participant__action--rotate";
            rotateButton.type = "button";
            rotateButton.dataset.resumeRotate = participant.rotate_url;
            rotateButton.textContent = "Обновить";
            rotateButton.setAttribute(
                "aria-label",
                `Обновить персональную ссылку участника ${participant.name}`,
            );

            const actions = document.createElement("div");
            actions.className = "participant__actions";
            actions.append(copyButton, rotateButton);

            row.append(avatar, main, actions);
            participantList.append(row);
        });
    }

    if (participantList) {
        participantList.addEventListener("click", async (event) => {
            const copyButton = event.target.closest("[data-resume-copy]");
            if (copyButton) {
                await copyText(copyButton.dataset.resumeCopy);
                copyButton.textContent = "Скопировано";
                window.setTimeout(() => {
                    copyButton.textContent = "Скопировать ссылку";
                }, 1800);
                return;
            }

            const rotateButton = event.target.closest("[data-resume-rotate]");
            if (!rotateButton) return;
            if (!window.confirm(
                "Обновить персональную ссылку? Старая ссылка и прежняя привязка браузера перестанут работать."
            )) return;

            rotateButton.disabled = true;
            rotateButton.textContent = "Обновляем…";
            try {
                const response = await fetch(rotateButton.dataset.resumeRotate, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": csrfToken,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                });
                if (!response.ok) throw new Error("resume rotation failed");
                const result = await response.json();
                await copyText(result.resume_url);
                const currentCopyButton = rotateButton
                    .closest(".participant")
                    ?.querySelector("[data-resume-copy]");
                if (currentCopyButton) {
                    currentCopyButton.dataset.resumeCopy = result.resume_url;
                    currentCopyButton.textContent = "Новая ссылка скопирована";
                }
                rotateButton.textContent = "Обновлено";
            } catch (_error) {
                rotateButton.disabled = false;
                rotateButton.textContent = "Повторить";
            }
        });
    }

    function renderRevealedVotes(votes) {
        if (!revealedVotes) return;
        revealedVotes.replaceChildren();
        votes.forEach((vote) => {
            const row = document.createElement("div");
            const name = document.createElement("span");
            name.textContent = vote.name;
            const value = document.createElement("strong");
            value.textContent = `${vote.value} ч`;
            row.append(name, value);
            revealedVotes.append(row);
        });
    }

    function renderQueueProgress(items, minimumParticipants) {
        (items || []).forEach((item) => {
            const row = document.querySelector(`[data-queue-item="${item.id}"]`);
            const votes = row?.querySelector("[data-queue-votes]");
            if (!votes) return;
            votes.textContent = `${item.vote_count} / ${minimumParticipants} голосов`;
            votes.classList.toggle(
                "queue-row__votes--ready",
                item.vote_count >= minimumParticipants,
            );
        });
    }

    async function refresh() {
        try {
            const response = await fetch(root.dataset.stateUrl, {
                headers: {"X-Requested-With": "XMLHttpRequest"},
                cache: "no-store",
            });
            if (!response.ok) return;
            const state = await response.json();
            renderParticipants(state.participants);
            if (participantPill) participantPill.textContent = state.participant_count;
            if (participantProgressSummary) {
                participantProgressSummary.textContent = `Завершили полностью: ${state.completed_participant_count} из ${state.participant_count}`;
            }
            if (votedCount) votedCount.textContent = state.voted_count;
            if (participantCount) participantCount.textContent = state.participant_count;
            if (minimumCount) minimumCount.textContent = state.minimum_participants;
            if (revealButton) revealButton.disabled = !state.minimum_reached;
            if (thresholdHint) {
                thresholdHint.textContent = state.minimum_reached
                    ? "Можно раскрывать карты"
                    : `До раскрытия нужно ещё голосов: ${state.votes_remaining}`;
                thresholdHint.classList.toggle("threshold-hint--ready", state.minimum_reached);
            }
            if (state.summary && averageValue) averageValue.textContent = state.summary.average;
            if (state.votes) renderRevealedVotes(state.votes);
            renderQueueProgress(state.queue_items, state.minimum_participants);
        } catch (_error) {
            // Следующая фоновая попытка восстановит состояние.
        }
    }

    refresh();
    window.setInterval(refresh, 1500);
})();
