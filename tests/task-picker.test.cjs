const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

const source = readFileSync(require('node:path').join(__dirname, '../static/js/task-picker.js'), 'utf8');

function picker(visibleSelection = true) {
    const control = (value = '') => ({value, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }});
    const search = control(), competency = control('all'), all = control(), none = control();
    const counter = {}, empty = {}, submit = {};
    const options = ['analysis', 'development_be', ''].map((type, index) => {
        const input = Object.assign(control(), {checked: true, disabled: false});
        return {hidden: false, input, dataset: {competency: type, searchText: `task ${index}`}, querySelector: () => input};
    });
    const controls = {
        '[data-task-search]': search, '[data-task-competency]': competency,
        '[data-task-select-all]': all, '[data-task-select-none]': none,
        '[data-task-count]': counter, '[data-task-empty]': empty, '[data-task-submit]': submit,
    };
    const element = {
        querySelectorAll: () => options, querySelector: selector => controls[selector],
        hasAttribute: () => visibleSelection, dispatchEvent() {},
    };
    vm.runInNewContext(source, {document: {querySelectorAll: () => [element]}, CustomEvent: class {}});
    return {
        options, counter, empty, submit,
        type(value) { competency.value = value; competency.handlers.change(); },
        search(value) { search.value = value; search.handlers.input(); },
        all() { all.handlers.click(); }, none() { none.handlers.click(); },
        submitted() { return options.filter(o => o.input.checked && !o.input.disabled).map(o => o.dataset.competency); },
    };
}

test('room filter prevents hidden preselected tasks from being submitted', () => {
    const room = picker();
    assert.equal(room.counter.textContent, '3 выбрано');
    room.type('analysis');
    assert.deepEqual(room.submitted(), ['analysis']);
    assert.equal(room.counter.textContent, '1 выбрано');
    room.type('');
    assert.deepEqual(room.submitted(), ['']);
    room.type('all');
    assert.equal(room.submitted().length, 3);
});

test('search and competency combine and an empty selection disables submission', () => {
    const room = picker();
    room.type('analysis');
    room.search('task 1');
    assert.deepEqual(room.submitted(), []);
    assert.equal(room.empty.hidden, false);
    assert.equal(room.submit.disabled, true);
    room.search('TASK 0');
    assert.deepEqual(room.submitted(), ['analysis']);
    assert.equal(room.empty.hidden, true);
    assert.equal(room.submit.disabled, false);
});

test('bulk selection applies only to visible tasks', () => {
    const room = picker();
    room.type('analysis');
    room.none();
    assert.equal(room.submit.disabled, true);
    room.type('all');
    assert.deepEqual(room.submitted(), ['development_be', '']);
    room.all();
    assert.equal(room.submitted().length, 3);
});

test('sprint picker keeps its existing selection semantics', () => {
    const sprint = picker(false);
    sprint.type('analysis');
    assert.equal(sprint.submitted().length, 3);
    assert.equal(sprint.counter.textContent, '3 выбрано');
});
