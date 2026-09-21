(() => {
    const picker = document.querySelector("[data-sprint-picker]");
    const data = document.getElementById("sprint-planning-data");
    if (!picker || !data || !picker.querySelector("[data-capacity-preview]")) return;
    const state = JSON.parse(data.textContent);
    const preview = picker.querySelector("[data-capacity-preview]");
    const format = (value) => Number(value).toLocaleString("ru-RU", {maximumFractionDigits: 2});
    function render() {
        const selected = Array.from(picker.querySelectorAll("[data-task-option]"))
            .filter((option) => option.querySelector("input").checked);
        const total = selected.reduce((sum, option) => sum + Number(option.dataset.estimate), 0);
        const lines = [];
        function line(label, used, capacity) {
            const node = document.createElement("span");
            let text = `${label}: ${format(used)} ч`;
            if (capacity !== null) {
                const remainder = Number(capacity) - used;
                text += ` / ${format(capacity)} ч · ` + (remainder < 0
                    ? `превышение ${format(-remainder)} ч` : `остаток ${format(remainder)} ч`);
                node.classList.toggle("selection-capacity--over", remainder < 0);
            } else text += " · ёмкость не задана";
            node.textContent = text;
            lines.push(node);
        }
        line(`После добавления (${selected.length} задач, ${format(total)} ч)`,
            Number(state.total) + total, state.capacity);
        state.competencies.forEach((row) => {
            const hours = selected.filter((option) => option.dataset.competency === row.key)
                .reduce((sum, option) => sum + Number(option.dataset.estimate), 0);
            line(row.label, Number(row.used) + hours, row.capacity);
        });
        const untyped = selected.filter((option) => !option.dataset.competency)
            .reduce((sum, option) => sum + Number(option.dataset.estimate), 0);
        if (untyped) line("Без типа (вне компетенческих ёмкостей)", untyped, null);
        preview.replaceChildren(...lines);
    }
    picker.addEventListener("taskpickerchange", render);
    render();
})();
