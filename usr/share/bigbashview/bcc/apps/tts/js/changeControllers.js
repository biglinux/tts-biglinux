import { API } from "./API.js"

// /** @type {HTMLSelectElement} */
// const RATESelect = document.querySelector('#RATE')

/** @type {HTMLSelectElement} */
const VOICESelect = document.querySelector('#VOICE')

// RATESelect.addEventListener("change", () => {
//     API.setRate(RATESelect.value)
// })

VOICESelect.addEventListener("change", () => {
    API.setVoice(VOICESelect.value)
})

/** @type {HTMLInputElement} */
const volumeRangeInput = document.querySelector("#volume-range")
/** @type {HTMLInputElement} */
const volumeInput = document.querySelector("#volume")

/** @type {HTMLInputElement} */
const pitchRangeInput = document.querySelector("#pitch-range")
/** @type {HTMLInputElement} */
const pitchInput = document.querySelector("#pitch")

/** @type {HTMLInputElement} */
const rateRangeInput = document.querySelector("#rate-range")
/** @type {HTMLInputElement} */
const rateInput = document.querySelector("#rate")

const shouldChange = (input1, input2) => input1.value !== input2.value

volumeRangeInput.addEventListener("input", () => {
    if (shouldChange(volumeInput, volumeRangeInput)) {
        volumeInput.value = volumeRangeInput.value
    }
})

volumeRangeInput.addEventListener("change", () => {
    API.setVolume(volumeRangeInput.value)
})

pitchRangeInput.addEventListener("change", () => {
    if (shouldChange(pitchInput, pitchRangeInput)) {
        pitchInput.value = pitchRangeInput.value
    }
})

pitchRangeInput.addEventListener("change", () => {
    API.setPitch(pitchRangeInput.value)
})

rateRangeInput.addEventListener("change", () => {
    if (shouldChange(rateInput, rateRangeInput)) {
        rateInput.value = rateRangeInput.value
    }
})

rateRangeInput.addEventListener("change", () => {
    API.setRate(rateRangeInput.value)
})