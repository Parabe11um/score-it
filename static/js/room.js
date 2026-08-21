(() => {
    const root = document.getElementById("room-app");
    if (!root) return;

    const waitingState = document.getElementById("waiting-state");
    const votingState = document.getElementById("voting-state");
    const revealedState = document.getElementById("revealed-state");
    const finishedState = document.getElementById("finished-state");
    const taskNumber = document.getElementById("room-task-number");
    const taskTitle = document.getElementById("room-task-title");
    const resultTaskNumber = document.getElementById("result-task-number");
    const resultTaskTitle = document.getElementById("result-task-title");
    const progress = document.getElementById("room-progress");
    const waitingProgress = document.getElementById("waiting-progress");
    const feedback = document.getElementById("vote-feedback");
    const average = document.getElementById("room-average");
    const votesList = document.getElementById("room-votes");
    const connectionState = document.getElementById("connection-state");
    const cards = Array.from(document.querySelectorAll(".poker-card"));
    const csrfToken = document.querySelector("#csrf-form [name=csrfmiddlewaretoken]")?.value;
    let requestInProgress = false;

    function showOnly(element) {
        [waitingState, votingState, revealedState, finishedState].forEach((item) => {
            item.hidden = item !== element;
        });
    }

    function selectCard(value) {
        cards.forEach((card) => {
            card.classList.toggle("poker-card--selected", Number(card.dataset.value) === value);
        });
    }

    function renderVotes(votes) {
        votesList.replaceChildren();
        votes.forEach((vote) => {
            const row = document.createElement("div");
            const name = document.createElement("span");
            name.textContent = vote.name;
            const value = document.createElement("strong");
            value.textContent = vote.value;
            row.append(name, value);
            votesList.append(row);
        });
    }

    function render(state) {
        if (state.session_status === "finished") {
            showOnly(finishedState);
            return;
        }
        if (!state.current_task || !state.round) {
            selectCard(null);
            if (state.queue?.total) {
                waitingProgress.textContent = state.queue.completed >= state.queue.total
                    ? `Очередь завершена: ${state.queue.completed} из ${state.queue.total}.`
                    : `В очереди ${state.queue.total} задач, оценено ${state.queue.completed}.`;
            } else {
                waitingProgress.textContent = "Организатор скоро запустит голосование.";
            }
            showOnly(waitingState);
            return;
        }

        if (state.round.status === "revealed") {
            resultTaskNumber.textContent = state.current_task.number;
            resultTaskTitle.textContent = state.current_task.title;
            average.textContent = state.round.average ?? "—";
            renderVotes(state.round.votes || []);
            showOnly(revealedState);
            return;
        }

        taskNumber.textContent = state.current_task.number;
        taskTitle.textContent = state.current_task.title;
        const queueLabel = state.queue?.current_position
            ? `Задача ${state.queue.current_position} из ${state.queue.total} · `
            : "";
        progress.textContent = `${queueLabel}голосов ${state.round.voted_count} из минимум ${state.round.minimum_participants}`;
        selectCard(state.round.my_vote);
        feedback.textContent = state.round.has_voted
            ? `Ваш выбор: ${state.round.my_vote}. Его можно изменить до раскрытия.`
            : "Можно изменить выбор до раскрытия.";
        showOnly(votingState);
    }

    async function refresh() {
        try {
            const response = await fetch(root.dataset.stateUrl, {
                headers: {"X-Requested-With": "XMLHttpRequest"},
                cache: "no-store",
            });
            if (response.status === 403) {
                window.location.reload();
                return;
            }
            if (!response.ok) throw new Error("state request failed");
            const state = await response.json();
            render(state);
            connectionState.classList.remove("connection-state--offline");
            connectionState.innerHTML = "<span></span> Подключено";
        } catch (_error) {
            connectionState.classList.add("connection-state--offline");
            connectionState.textContent = "Нет соединения · повторяем";
        }
    }

    async function sendVote(value) {
        if (requestInProgress) return;
        requestInProgress = true;
        cards.forEach((card) => { card.disabled = true; });
        const body = new FormData();
        body.append("value", String(value));
        try {
            const response = await fetch(root.dataset.voteUrl, {
                method: "POST",
                body,
                headers: {"X-CSRFToken": csrfToken, "X-Requested-With": "XMLHttpRequest"},
            });
            if (!response.ok) throw new Error("vote request failed");
            selectCard(value);
            feedback.textContent = `Ваш выбор: ${value}. Его можно изменить до раскрытия.`;
            await refresh();
        } catch (_error) {
            feedback.textContent = "Не удалось сохранить голос. Попробуйте ещё раз.";
        } finally {
            requestInProgress = false;
            cards.forEach((card) => { card.disabled = false; });
        }
    }

    cards.forEach((card) => {
        card.addEventListener("click", () => sendVote(Number(card.dataset.value)));
    });

    refresh();
    window.setInterval(refresh, 1500);
})();
