const searchInput = document.getElementById("search-input");
const fuelTypeSelect = document.getElementById("fuel-type-select");
const searchButton = document.getElementById("fuel-search-button");
const tableBody = document.getElementById("table-body");
const tableStatus = document.getElementById("fuel-table-status");

function formatPeso(value) {
  const numberValue = Number(value);

  if (Number.isNaN(numberValue)) {
    return "-";
  }

  return `PHP ${numberValue.toFixed(2)}`;
}

function setStatus(message) {
  tableStatus.textContent = message;
}

function renderRows(rows) {
  tableBody.innerHTML = "";

  if (!rows.length) {
    setStatus("No fuel prices found for that location and fuel type.");
    return;
  }

  const fragment = document.createDocumentFragment();

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const cells = [
      row.brand_name,
      row.city_name,
      row.province_name,
      row.fuel_type,
      formatPeso(row.price_min),
      formatPeso(row.price_max),
      formatPeso(row.average_price),
    ];

    cells.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value || "-";
      tr.appendChild(td);
    });

    fragment.appendChild(tr);
  });

  tableBody.appendChild(fragment);
  setStatus(`Showing ${rows.length} result${rows.length === 1 ? "" : "s"}.`);
}

async function executeSearch() {
  const searchTerm = searchInput.value.trim();
  const fuelType = fuelTypeSelect.value;

  if (!searchTerm) {
    tableBody.innerHTML = "";
    setStatus("Enter a city or province before searching.");
    searchInput.focus();
    return;
  }

  setStatus("Loading fuel prices...");

  const params = new URLSearchParams({
    q: searchTerm,
  });

  if (fuelType) {
    params.set("fuel_type", fuelType);
  }

  try {
    const response = await fetch(`/api/fuel-prices?${params.toString()}`);

    if (!response.ok) {
      throw new Error("Fuel price request failed.");
    }

    const rows = await response.json();
    renderRows(rows);
  } catch (error) {
    console.error(error);
    tableBody.innerHTML = "";
    setStatus("Could not load fuel prices. Please try again.");
  }
}

searchButton.addEventListener("click", executeSearch);
fuelTypeSelect.addEventListener("change", () => {
  if (searchInput.value.trim()) {
    executeSearch();
  }
});
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    executeSearch();
  }
});
