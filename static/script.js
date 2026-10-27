
const questionBox = document.getElementById("question");
const askButton = document.getElementById("askButton");
const resultBox = document.getElementById("result");
const answerBox = document.getElementById("answer");

const tabs = document.querySelectorAll(".tab");
const exampleButtons = document.querySelectorAll(".example");

const imageInput = document.getElementById("imageInput");
const chooseImageButton = document.getElementById("chooseImageButton");
const fileName = document.getElementById("fileName");
const imagePreview = document.getElementById("imagePreview");
const imagePreviewContainer = document.getElementById("imagePreviewContainer");
const removeImageButton = document.getElementById("removeImageButton");

let selectedSection = "Math";
let previewUrl = null;

// Switch between SAT sections
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    selectedSection = tab.dataset.section;
  });
});

// Fill the question box with an example
exampleButtons.forEach((button) => {
  button.addEventListener("click", () => {
    questionBox.value = button.dataset.question;
    questionBox.focus();
  });
});

// Open the custom image picker
chooseImageButton.addEventListener("click", () => {
  imageInput.click();
});

// Select and preview an image
imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];

  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  if (!file) {
    imagePreviewContainer.hidden = true;
    imagePreview.removeAttribute("src");
    fileName.textContent = "No image selected";
    return;
  }

  const allowedTypes = ["image/jpeg", "image/png", "image/webp"];

  if (!allowedTypes.includes(file.type)) {
    imageInput.value = "";
    imagePreviewContainer.hidden = true;
    imagePreview.removeAttribute("src");
    fileName.textContent = "No image selected";
    alert("Please choose a JPG, PNG, or WebP image.");
    return;
  }

  if (file.size > 10 * 1024 * 1024) {
    imageInput.value = "";
    imagePreviewContainer.hidden = true;
    imagePreview.removeAttribute("src");
    fileName.textContent = "No image selected";
    alert("Please choose an image smaller than 10 MB.");
    return;
  }

  previewUrl = URL.createObjectURL(file);
  imagePreview.src = previewUrl;
  fileName.textContent = file.name;
  imagePreviewContainer.hidden = false;
});

// Remove the selected image
removeImageButton.addEventListener("click", () => {
  imageInput.value = "";
  imagePreviewContainer.hidden = true;
  imagePreview.removeAttribute("src");
  fileName.textContent = "No image selected";

  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
});

// Send the question and optional image to Flask
askButton.addEventListener("click", async () => {
  const question = questionBox.value.trim();
  const image = imageInput.files[0];

  if (!question && !image) {
    answerBox.textContent =
      "Please type an SAT question or upload an image first.";
    resultBox.hidden = false;
    questionBox.focus();
    return;
  }

  const formData = new FormData();
  formData.append("question", question);
  formData.append("section", selectedSection);

  if (image) {
    formData.append("image", image);
  }

  askButton.disabled = true;
  askButton.textContent = "Analyzing your question...";
  answerBox.textContent =
    "Your AI tutor is working through the problem. Please wait...";

  resultBox.hidden = false;

  try {
    const csrfToken = document.querySelector(
      'meta[name="csrf-token"]'
    ).content;


const response = await fetch("/ask", {
  method: "POST",
  headers: {
    "X-CSRFToken": csrfToken,
  },
  body: formData,
});
   const responseText = await response.text();

let data;

try {
  data = JSON.parse(responseText);
} catch {
  console.error("Server response:", response.status, responseText);

  throw new Error(
    `Server returned HTML instead of JSON (HTTP ${response.status}). Check Render Logs.`
  );
}

    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    answerBox.textContent =
      data.answer || "No answer was returned. Please try again.";
  } catch (error) {
    answerBox.textContent =
      "Something went wrong: " + error.message +
      "\n\nCheck the VS Code terminal for more details.";
    console.error("SAT Tutor error:", error);
  } finally {
    askButton.disabled = false;
    askButton.innerHTML = 'Explain my answer <span>→</span>';
  }
});

console.log("SAT Tutor JavaScript loaded successfully.");


/* Paste images directly from the clipboard */
document.addEventListener("paste", (event) => {
  const items = event.clipboardData?.items;

  if (!items) return;

  for (const item of items) {
    if (!item.type.startsWith("image/")) continue;

    const image = item.getAsFile();

    if (!image) continue;

    event.preventDefault();

    if (image.size > 10 * 1024 * 1024) {
      alert("The image is too large. Maximum size is 10 MB.");
      return;
    }

    const allowedTypes = ["image/png", "image/jpeg", "image/webp"];

    if (!allowedTypes.includes(image.type)) {
      alert("Please paste a PNG, JPG, or WebP image.");
      return;
    }

    // Put the pasted image into the existing file input.
    const file = new File(
      [image],
      `pasted-image.${image.type === "image/png" ? "png" : image.type === "image/webp" ? "webp" : "jpg"}`,
      { type: image.type }
    );

    const transfer = new DataTransfer();
    transfer.items.add(file);
    imageInput.files = transfer.files;

    // Trigger the existing preview and validation logic.
    imageInput.dispatchEvent(new Event("change", { bubbles: true }));

    break;
  }
});
