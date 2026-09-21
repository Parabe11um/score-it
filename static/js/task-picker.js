(() => {
    document.querySelectorAll("[data-task-picker]").forEach((picker) => {
        const options = Array.from(picker.querySelectorAll("[data-task-option]"));
        const search = picker.querySelector("[data-task-search]");
        const selectAll = picker.querySelector("[data-task-select-all]");
        const selectNone = picker.querySelector("[data-task-select-none]");
        const counter = picker.querySelector("[data-task-count]");
        const competency = picker.querySelector("[data-task-competency]");

        function checkboxFor(option) {
            return option.querySelector('input[type="checkbox"]');
        }

        function updateCounter() {
            const selected = options.filter((option) => checkboxFor(option)?.checked).length;
            if (counter) counter.textContent = `${selected} выбрано`;
            picker.dispatchEvent(new CustomEvent("taskpickerchange"));
        }

        function visibleOptions() {
            return options.filter((option) => !option.hidden);
        }

        function filterOptions() {
            const query = search?.value.trim().toLocaleLowerCase("ru") || "";
            const type = competency ? competency.value : "all";
            options.forEach((option) => {
                option.hidden = (Boolean(query) && !option.dataset.searchText.includes(query)) ||
                    (type !== "all" && option.dataset.competency !== type);
            });
        }
        search?.addEventListener("input", filterOptions);
        competency?.addEventListener("change", filterOptions);

        selectAll?.addEventListener("click", () => {
            visibleOptions().forEach((option) => {
                const checkbox = checkboxFor(option);
                if (checkbox) checkbox.checked = true;
            });
            updateCounter();
        });

        selectNone?.addEventListener("click", () => {
            visibleOptions().forEach((option) => {
                const checkbox = checkboxFor(option);
                if (checkbox) checkbox.checked = false;
            });
            updateCounter();
        });

        options.forEach((option) => checkboxFor(option)?.addEventListener("change", updateCounter));
        updateCounter();
    });
})();
