# Israeli Trip Planner 🇮🇱

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Active](https://img.shields.io/badge/Status-Active-success.svg)]()

> A smart, intuitive, and comprehensive web application designed to help users plan their perfect trip across Israel. 

### 🚀 **[Click here to view the Live Demo](https://israeli-trip-planner.vercel.app)**

---

## About The Project

Planning a trip in Israel can sometimes feel overwhelming. **Israeli Trip Planner** is designed to simplify this process by providing a clean, centralized platform where users can easily generate, customize, and manage daily itineraries across different regions of the country. 

The application handles the logic behind travel times, regional routes, and constraints automatically, letting users focus entirely on the experience.

### Key Features
* **Smart Itinerary Generation:** Automatically plans balanced day trips based on region, travel time limits, and preferences.
* **Interactive Editing:** Easily remove or swap stops in your itinerary with real-time updates.
* **Shabbat & Dietary Awareness:** Tailored logic to support shabbat-observant travelers and meal scheduling.
* **Responsive Design:** Fully optimized for smooth usage across desktop and mobile devices.

---

## Design & Accessibility Philosophy

A major focus during the development of this project was keeping the interface distraction-free and intuitive:
* **Intentional Minimalism:** Avoiding visual clutter and unnecessary animations to reduce cognitive load and keep the user focused on the planning process.
* **Clear User Journey:** Every action has a predictable flow, ensuring a smooth and straightforward experience from start to finish.

---

## Architecture & Technical Decisions

The project is structured with a clear separation of concerns, dividing the backend logic from the frontend presentation layer to ensure maintainability and clean code practices:

* **Backend (FastAPI):** Built with a strict separation between routes, domain logic, planners, and repositories. The API handles validation and schedule enforcement to ensure that no invalid itinerary is ever returned to the client.
* **Frontend (Vercel):** Communicates seamlessly with the REST API hosted on Render, managing state and rendering dynamic UI components based on user interactions.
* **Why this approach?** 
    * *Modularity:* Isolating the planning rules and data management makes the codebase much easier to debug, test, and extend.
    * *Robustness:* Centralized validation rules protect the application from edge cases during itinerary edits.

---

## Built With

| Category | Technologies Used |
| :--- | :--- |
| **Frontend** | HTML5, CSS3, JavaScript / TypeScript (Vercel) |
| **Backend** | Python, FastAPI, Uvicorn (Render) |
| **Tools & Deployment** | Git, GitHub, REST APIs |

---

## Getting Started

Follow these instructions to set up and run the project locally on your machine for development and testing.

### Prerequisites
Make sure you have Python installed on your system along with pip.

### Local Installation

1. **Clone the repository:**
   ```sh
   git clone [https://github.com/KarinMalka1/Israeli-Trip-Planner.git](https://github.com/KarinMalka1/Israeli-Trip-Planner.git)
   cd Israeli-Trip-Planner
