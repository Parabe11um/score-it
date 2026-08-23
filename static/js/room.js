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
    const resultNote = document.getElementById("result-note");
    const connectionState = document.getElementById("connection-state");
    const taskNavigation = document.getElementById("task-navigation");
    const previousTask = document.getElementById("previous-task");
    const nextTask = document.getElementById("next-task");
    const personalProgress = document.getElementById("personal-progress");
    const resumeLinkButton = document.getElementById("copy-resume-link");
    const cards = Array.from(document.querySelectorAll(".poker-card"));
    const csrfToken = document.querySelector("#csrf-form [name=csrfmiddlewaretoken]")?.value;
    let requestInProgress = false;
    let navigationInProgress = false;
    let atLastTask = false;
    let nextAction = "next";

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

    if (resumeLinkButton) {
        resumeLinkButton.addEventListener("click", async () => {
            await copyText(resumeLinkButton.dataset.resumeUrl);
            resumeLinkButton.textContent = "Ссылка скопирована";
            window.setTimeout(() => {
                resumeLinkButton.textContent = "Ссылка для продолжения";
            }, 1800);
        });
    }

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
        if (state.participant_completed) {
            window.location.reload();
            return;
        }
        if (state.session_status === "finished") {
            taskNavigation.hidden = true;
            showOnly(finishedState);
            return;
        }
        if (!state.current_task || !state.round) {
            taskNavigation.hidden = true;
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

        taskNavigation.hidden = false;
        previousTask.disabled = !state.queue.has_previous || navigationInProgress;
        atLastTask = !state.queue.has_next;
        nextAction = "next";
        nextTask.disabled = navigationInProgress;
        nextTask.textContent = "Вперёд →";

        if (atLastTask && state.queue.all_voted) {
            nextAction = "complete";
            nextTask.textContent = "Завершить оценку";
            personalProgress.textContent = `Оценены все задачи: ${state.queue.total}`;
        } else if (
            atLastTask
            && state.queue.first_missing_task_id !== state.current_task.id
        ) {
            nextAction = "missing";
            nextTask.textContent = "К первой пропущенной →";
            personalProgress.textContent = `Осталось оценить: ${state.queue.missing}`;
        } else if (atLastTask) {
            nextAction = "current_vote_required";
            nextTask.textContent = "Выберите оценку";
            nextTask.disabled = true;
            personalProgress.textContent = `Осталось оценить: ${state.queue.missing} · начните с текущей`;
        } else {
            personalProgress.textContent = `Оценено вами: ${state.queue.voted} из ${state.queue.total} · осталось ${state.queue.missing}`;
        }

        if (state.round.status === "revealed" || state.round.status === "closed") {
            resultTaskNumber.textContent = state.current_task.number;
            resultTaskTitle.textContent = state.current_task.title;
            average.textContent = state.round.average ?? "—";
            renderVotes(state.round.votes || []);
            resultNote.textContent = state.round.status === "closed"
                ? "Итоговая оценка сохранена. Можно перейти к другой задаче."
                : "Голоса раскрыты. Можно перейти к другой задаче.";
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
            ? `Ваш выбор: ${state.round.my_vote}. Можно перейти дальше или изменить голос.`
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
            feedback.textContent = `Ваш выбор: ${value}. Голос сохранён — можно перейти дальше.`;
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

    async function navigate(direction) {
        if (navigationInProgress || requestInProgress) return;
        navigationInProgress = true;
        previousTask.disabled = true;
        nextTask.disabled = true;
        const body = new FormData();
        body.append("direction", direction);
        try {
            const response = await fetch(root.dataset.navigateUrl, {
                method: "POST",
                body,
                headers: {"X-CSRFToken": csrfToken, "X-Requested-With": "XMLHttpRequest"},
            });
            if (!response.ok) throw new Error("navigation request failed");
            selectCard(null);
        } catch (_error) {
            feedback.textContent = "Не удалось перейти к другой задаче. Попробуйте ещё раз.";
            personalProgress.textContent = "Не удалось перейти к другой задаче.";
        } finally {
            navigationInProgress = false;
            await refresh();
        }
    }

    async function completeVoting() {
        if (navigationInProgress || requestInProgress) return;
        navigationInProgress = true;
        previousTask.disabled = true;
        nextTask.disabled = true;
        nextTask.textContent = "Завершаем…";
        try {
            const response = await fetch(root.dataset.completeUrl, {
                method: "POST",
                headers: {"X-CSRFToken": csrfToken, "X-Requested-With": "XMLHttpRequest"},
            });
            const result = await response.json();
            if (!response.ok) {
                if (result.error === "incomplete_tasks") {
                    personalProgress.textContent = `Осталось оценить: ${result.missing}`;
                }
                throw new Error("completion request failed");
            }
            window.location.reload();
        } catch (_error) {
            personalProgress.textContent = "Не удалось завершить оценку. Попробуйте ещё раз.";
            navigationInProgress = false;
            await refresh();
        }
    }

    previousTask.addEventListener("click", () => navigate("previous"));
    nextTask.addEventListener("click", () => {
        if (nextAction === "complete") {
            completeVoting();
            return;
        }
        if (nextAction === "missing") {
            navigate("missing");
            return;
        }
        if (nextAction === "next") navigate("next");
    });

    refresh();
    window.setInterval(refresh, 1500);
})();
