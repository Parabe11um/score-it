document.querySelectorAll("[data-capacity-source-form]").forEach((form) => {
    const source = form.querySelector('[name="capacity_source"]');
    const manual = form.querySelector("[data-manual-capacity]");
    if (!source || !manual) return;
    const update = () => { manual.hidden = source.value === "team"; };
    source.addEventListener("change", update);
    update();
});
