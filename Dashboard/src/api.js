const API_BASE = "http://127.0.0.1:5000/api/forecast";

export const getSkus = () =>
  fetch(`${API_BASE}/skus`).then(res => res.json());

export const getDashboard = (itemCode) =>
  fetch(`${API_BASE}/dashboard`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ item_code: itemCode }),
  }).then(res => res.json());

export const getHealth = () =>
  fetch(`${API_BASE}/health`).then(res => res.json());