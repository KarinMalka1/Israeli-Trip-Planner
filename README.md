# Israeli Trip Planner 🇮🇱

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Active](https://img.shields.io/badge/Status-Active-success.svg)]()

> A smart, intuitive, and comprehensive web application designed to help users plan their perfect trip across Israel. 

### 🚀 **[Click here to view the Live Demo](https://israeli-trip-planner.vercel.app)**

---

## 📖 About The Project

Planning a trip in Israel can be overwhelming due to the sheer number of historical sites, nature reserves, and culinary spots. **Israeli Trip Planner** simplifies this process by providing a centralized platform where users can discover, organize, and plan their itineraries seamlessly. 

### ✨ Key Features
*   **Interactive Itinerary Builder:** Drag and drop attractions to plan your daily schedule.
*   **Categorized Discoveries:** Filter locations by Nature, History, Food, or Culture.
*   **User Accounts:** Save your favorite spots and access your planned trips from any device.
*   **Responsive Design:** Fully optimized for mobile and desktop usage.

---

## 🎨 Design & Accessibility Philosophy

We believe that planning a trip should be exciting, not overwhelming. A major focus during the development of this project was **accessibility for users with ADHD** (Attention Deficit Hyperactivity Disorder). 

*   **Intentional Minimalism:** We deliberately chose a clean, distraction-free UI. By removing unnecessary animations, pop-ups, and visual clutter, we reduce cognitive load.
*   **Clear Visual Hierarchy:** High contrast, readable typography, and intuitive navigation ensure that users can focus strictly on their main task—building their itinerary—without getting lost in the interface.
*   **Action-Oriented:** Every screen has a clear primary action, preventing decision paralysis and making the user journey smooth and predictable.

---

## 🏗️ Architecture & Technical Decisions

When building this project, we prioritized maintainability, scalability, and clean code principles. Instead of writing a monolithic application, we made specific architectural decisions to ensure our codebase remains robust:

*   **Separation of Concerns:** We strictly separated the UI layer (Components/Views) from the business logic and state management. This makes the code easier to test and debug.
*   **Modular Folder Structure:** The project is divided logically by features rather than file types (e.g., `[Insert your folder structure here, e.g., /auth, /trips, /shared]`). This encapsulation means that changes in one feature won't unintentionally break another.
*   **Why we chose this approach:** 
    *   *Readability:* New developers can quickly understand the flow of data.
    *   *Reusability:* UI components (like buttons, cards, and modals) are built as generic elements that can be reused across the app, keeping the codebase DRY (Don't Repeat Yourself).
    *   *API Integration:* [Optional: Explain how you separated API calls into a dedicated service folder so components don't fetch data directly].

---

## 💻 Built With

| Category | Technologies Used |
| :--- | :--- |
| **Frontend** | [e.g., React.js, Tailwind CSS, HTML5] |
| **Backend** | [e.g., Node.js, Express.js] |
| **Database** | [e.g., MongoDB, PostgreSQL] |
| **Tools** | Git, GitHub, [e.g., Vercel/Render] |

---

## 🛠️ Getting Started

Follow these instructions to set up the project locally on your machine for development and testing purposes.

### Prerequisites
Make sure you have the following installed:
*   [Node.js](https://nodejs.org/) (v14.0 or higher)
*   npm
  ```sh
  npm install npm@latest -g
