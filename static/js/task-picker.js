(() => {
    document.querySelectorAll("[data-task-picker]").forEach((picker) => {
        const options = Array.from(picker.querySelectorAll("[data-task-option]"));
        const search = picker.querySelector("[data-task-search]");
        const selectAll = picker.querySelector("[data-task-select-all]");
        const selectNone = picker.querySelector("[data-task-select-none]");
        const counter = picker.querySelector("[data-task-count]");
        const competency = picker.querySelector("[data-task-competency]");
        const visibleSelection = picker.hasAttribute("data-task-visible-selection");
        const empty = picker.querySelector("[data-task-empty]");
        const submit = picker.querySelector("[data-task-submit]");

        function checkboxFor(option) {
            return option.querySelector('input[type="checkbox"]');
        }

        function updateCounter() {
            const selected = options.filter((option) => checkboxFor(option)?.checked &&
                (!visibleSelection || !option.hidden)).length;
            if (counter) counter.textContent = `${selected} выбрано`;
            if (submit) submit.disabled = selected === 0;
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
                const checkbox = checkboxFor(option);
                if (visibleSelection && checkbox) checkbox.disabled = option.hidden;
            });
            if (empty) empty.hidden = options.length === 0 || visibleOptions().length > 0;
            updateCounter();
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
        filterOptions();
    });
})();
