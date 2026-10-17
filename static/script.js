// static/script.js

const boxes = document.querySelectorAll('.file-box');
let active = null;

boxes.forEach(box => {
  box.addEventListener('click', e => {
    if (box.classList.contains('focused')) {
      resetFocus();
    } else {
      setFocus(box);
    }
    e.stopPropagation();
  });
});

document.addEventListener('click', resetFocus);

document.addEventListener('click', event => {
  const closeButton = event.target.closest('.flash-close');
  if (closeButton) {
    closeButton.closest('.flash-messages')?.remove();
  }
});

function setFocus(target) {
  active = target;
  boxes.forEach(box => {
    box.classList.remove('focused', 'inactive');
    if (box !== target) {
      box.classList.add('inactive');
    } else {
      box.classList.add('focused');
    }
  });
}

function resetFocus() {
  active = null;
  boxes.forEach(box => {
    box.classList.remove('focused', 'inactive');
  });
}
/*
document.addEventListener("DOMContentLoaded", () => {
  //const maxSize = 2 * 1024 * 1024; // 2 MB in bytes
  const maxSize = 2 * 1024; // 2 KB in bytes (for testing purposes, change to 2 * 1024 * 1024 for actual use)

  const fileInputs = document.querySelectorAll("#xmlfileInput, #yamlfileInput, #jsonfileInput");

  fileInputs.forEach(input => {
    const form = input.closest("form");

    if (form) {
      form.addEventListener("submit", (e) => {
        const file = input.files[0];

        if (file && file.size > maxSize) {
          e.preventDefault();
          alert(`⚠️ The file selected in "${input.id}" is too large.\nMax allowed size is 2 MB.`);
        }
      });
    }
  });
});
*/
document.addEventListener("DOMContentLoaded", () => {
  const maxSize = 2 * 1024 * 1024; // 2 MB
  const fileInputs = document.querySelectorAll("#xmlfileInput, #yamlfileInput, #jsonfileInput");

  fileInputs.forEach(input => {
    const form = input.closest("form");

    // 🔹 Create a display area dynamically (optional fallback if no span)
    let display = document.createElement("div");
    display.className = "file-info";
    input.parentNode.insertBefore(display, input.nextSibling);

    input.addEventListener("change", () => {
      const file = input.files[0];
      if (file) {
        const sizeKB = (file.size / 1024).toFixed(1);
        display.textContent = `📄 ${file.name} (${sizeKB} KB)`;
      } else {
        display.textContent = "";
      }
    });

    form.addEventListener("submit", (e) => {
      const file = input.files[0];
      if (file && file.size > maxSize) {
        e.preventDefault();
        alert(`⚠️ ${file.name} is too large.\nMax allowed: 2 MB.`);
      }
    });
  });
});

