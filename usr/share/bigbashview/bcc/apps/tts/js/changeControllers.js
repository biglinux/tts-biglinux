import { API } from "./API.js"

/** @type {HTMLSelectElement} */
const PITCHSelect = document.querySelector('#PITCH')

/** @type {HTMLSelectElement} */
const RATESelect = document.querySelector('#RATE')

/** @type {HTMLSelectElement} */
const VOICESelect = document.querySelector('#VOICE')

PITCHSelect.addEventListener("change", () => {
    API.setPitch(PITCHSelect.value)
})

RATESelect.addEventListener("change", () => {
    API.setRate(RATESelect.value)
})

VOICESelect.addEventListener("change", () => {
    API.setVoice(VOICESelect.value)
})

/** @type {HTMLInputElement} */
const volumeRangeInput = document.querySelector("#volume-range")
/** @type {HTMLInputElement} */
const volumeInput = document.querySelector("#volume")

const shouldChange = (input1, input2) => input1.value !== input2.value

volumeRangeInput.addEventListener("input", () => {
    if (shouldChange(volumeInput, volumeRangeInput)) {
        volumeInput.value = volumeRangeInput.value
    }
})

volumeRangeInput.addEventListener("change", () => {
    API.setVolume(volumeRangeInput.value)
})