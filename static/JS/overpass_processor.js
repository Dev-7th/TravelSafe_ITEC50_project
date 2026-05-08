let stationLayer = L.layerGroup().addTo(map);

const BRAND_ALIASES = {
  CALTEX: ["CALTEX", "CALTEX HAVOLINE"],
  "FLYING V": ["FLYING V", "FLYING-V"],
  PETRON: ["PETRON"],
  PHOENIX: ["PHOENIX", "PHOENIX FUELS"],
  PTT: ["PTT"],
  SEAOIL: ["SEAOIL", "SEA OIL"],
  SHELL: ["SHELL", "PILIPINAS SHELL"],
  TOTAL: ["TOTAL", "TOTALENERGIES", "TOTAL ENERGIES"],
  UNIOIL: ["UNIOIL", "UNI OIL"],
};

function normalizeBrand(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, " ")
    .replace(/\b(GASOLINE|GAS|STATION|SERVICE|SERVICES|PHILIPPINES|PH)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stationDisplayName(station) {
  const tags = station.tags || {};
  return tags.brand || tags.name || tags.operator || "Fuel Station";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function matchStationBrand(station, brandPrices) {
  const stationText = normalizeBrand([
    station.tags && station.tags.brand,
    station.tags && station.tags.name,
    station.tags && station.tags.operator,
  ].filter(Boolean).join(" "));

  for (const brandName of Object.keys(brandPrices || {})) {
    const candidates = BRAND_ALIASES[brandName] || [brandName];
    if (candidates.some((candidate) => stationText.includes(normalizeBrand(candidate)))) {
      return brandName;
    }
  }

  return null;
}

function markerIcon(isCheapest, hasPriceMatch) {
  const color = isCheapest ? "#16a34a" : hasPriceMatch ? "#2563eb" : "#6b7280";

  return L.divIcon({
    className: "fuel-station-pin",
    html: `<span style="background:${color}"></span>`,
    iconSize: [24, 24],
    iconAnchor: [12, 24],
    popupAnchor: [0, -22],
  });
}

function formatPeso(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "N/A";
  }
  return `PHP ${Number(value).toFixed(2)}`;
}

function priceRangeText(fuel) {
  if (fuel.price_min === fuel.price_max) {
    return formatPeso(fuel.price_min);
  }

  return `${formatPeso(fuel.price_min)} - ${formatPeso(fuel.price_max)}`;
}

function buildPopup(station, brandName, brandData, cityPriceData, isCheapest) {
  const tags = station.tags || {};
  const stationName = escapeHtml(stationDisplayName(station));
  const brandLabel = escapeHtml(brandName || tags.brand || "Brand not matched");
  const rows = brandData
    ? brandData.fuel_types.map((fuel) => `
        <tr>
          <td>${escapeHtml(fuel.fuel_type || "Fuel")}</td>
          <td>${priceRangeText(fuel)}</td>
        </tr>
      `).join("")
    : `<tr><td colspan="2">No matching price in the city database.</td></tr>`;

  return `
    <div class="station-popup">
      <strong>${stationName}</strong>
      <p>${brandLabel}${isCheapest ? " - cheapest matched brand" : ""}</p>
      <table>
        <tbody>${rows}</tbody>
      </table>
      <small>${escapeHtml(cityPriceData.city || cityPriceData.query_city || "Selected city")}${cityPriceData.latest_date ? ` prices as of ${escapeHtml(cityPriceData.latest_date)}` : ""}</small>
    </div>
  `;
}

function stationLatLng(station) {
  return [
    station.lat || station.center.lat,
    station.lon || station.center.lon,
  ];
}

function renderFuelStations(stations, cityPriceData) {
  stationLayer.clearLayers();

  const brandPrices = cityPriceData.brands || {};
  const cheapestBrands = new Set(cityPriceData.lowest_brands || []);
  const bounds = [];

  stations.forEach((station) => {
    const brandName = matchStationBrand(station, brandPrices);
    const brandData = brandName ? brandPrices[brandName] : null;
    const isCheapest = brandName && cheapestBrands.has(brandName);
    const latLng = stationLatLng(station);

    L.marker(latLng, {
      icon: markerIcon(isCheapest, Boolean(brandData)),
      title: stationDisplayName(station),
    })
      .bindPopup(buildPopup(station, brandName, brandData, cityPriceData, isCheapest))
      .addTo(stationLayer);

    bounds.push(latLng);
  });

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [35, 35], maxZoom: 15 });
  }

  return {
    totalStations: stations.length,
    matchedStations: stations.filter((station) => matchStationBrand(station, brandPrices)).length,
  };
}
