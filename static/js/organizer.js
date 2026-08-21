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
    const votedCount = document.getElementById("voted-count");
    const participantCount = document.getElementById("participant-count");
    const minimumCount = document.getElementById("minimum-count");
    const thresholdHint = document.getElementById("threshold-hint");
    const revealButton = document.getElementById("reveal-button");
    const revealedVotes = document.getElementById("revealed-votes");
    const averageValue = document.getElementById("average-value");

    function initial(name) {
        return Array.from(name.trim())[0]?.toUpperCase() || "?";
    }

    function renderParticipants(participants, roundStatus) {
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
            status.className = `participant__state${participant.voted ? " participant__state--ready" : ""}`;
            if (!roundStatus) status.textContent = "В комнате";
            else status.textContent = participant.voted ? "Готово" : "Думает";

            row.append(avatar, name, status);
            participantList.append(row);
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
            value.textContent = vote.value;
            row.append(name, value);
            revealedVotes.append(row);
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
            renderParticipants(state.participants, state.round_status);
            if (participantPill) participantPill.textContent = state.participant_count;
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
        } catch (_error) {
            // Следующая фоновая попытка восстановит состояние.
        }
    }

    refresh();
    window.setInterval(refresh, 1500);
})();
