(() => {
    document.querySelectorAll("[data-task-picker]").forEach((picker) => {
        const options = Array.from(picker.querySelectorAll("[data-task-option]"));
        const search = picker.querySelector("[data-task-search]");
        const selectAll = picker.querySelector("[data-task-select-all]");
        const selectNone = picker.querySelector("[data-task-select-none]");
        const counter = picker.querySelector("[data-task-count]");

        function checkboxFor(option) {
            return option.querySelector('input[type="checkbox"]');
        }

        function updateCounter() {
            const selected = options.filter((option) => checkboxFor(option)?.checked).length;
            if (counter) counter.textContent = `${selected} выбрано`;
        }

        function visibleOptions() {
            return options.filter((option) => !option.hidden);
        }

        search?.addEventListener("input", () => {
            const query = search.value.trim().toLocaleLowerCase("ru");
            options.forEach((option) => {
                option.hidden = Boolean(query) && !option.dataset.searchText.includes(query);
            });
        });

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
