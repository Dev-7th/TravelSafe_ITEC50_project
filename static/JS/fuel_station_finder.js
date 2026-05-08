const OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter";
const OVERPASS_MIN_DELAY_MS = 1200;
let overpassQueue = Promise.resolve();
let lastOverpassRequestAt = 0;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function queueOverpassRequest(query) {
  const requestTask = overpassQueue.then(async () => {
    const elapsed = Date.now() - lastOverpassRequestAt;
    if (elapsed < OVERPASS_MIN_DELAY_MS) {
      await wait(OVERPASS_MIN_DELAY_MS - elapsed);
    }

    lastOverpassRequestAt = Date.now();
    const response = await fetch(OVERPASS_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      },
      body: new URLSearchParams({ data: query }),
    });

    if (!response.ok) {
      throw new Error(`Overpass request failed with status ${response.status}. Please try again in a moment.`);
    }

    return response.json();
  });

  overpassQueue = requestTask.catch(() => {});
  return requestTask;
}

function escapeOverpassString(value) {
  return String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function citySearchVariants(cityName) {
  const cleanName = String(cityName || "").trim();
  const withoutCityOf = cleanName.replace(/^City of\s+/i, "").trim();
  const withoutCity = cleanName.replace(/\s+City$/i, "").trim();
  const baseName = withoutCityOf || withoutCity || cleanName;
  const withCity = /\bCity$/i.test(baseName) ? baseName : `${baseName} City`;
  const cityOf = /^City of\s+/i.test(cleanName) ? cleanName : `City of ${baseName}`;

  return [...new Set([cleanName, withoutCityOf, withoutCity, withCity, cityOf].filter(Boolean))];
}

function buildFuelStationQuery(cityName) {
  const areaSelectors = citySearchVariants(cityName)
    .map((name) => `area["boundary"="administrative"]["name"="${escapeOverpassString(name)}"];`)
    .join("\n");

  return `
    [out:json][timeout:35];
    (
      ${areaSelectors}
    )->.searchAreas;
    (
      node["amenity"="fuel"](area.searchAreas);
      way["amenity"="fuel"](area.searchAreas);
      relation["amenity"="fuel"](area.searchAreas);
    );
    out center tags;
  `;
}

function buildFuelStationRadiusQuery(lat, lon, radiusMeters = 10000) {
  return `
    [out:json][timeout:35];
    (
      node["amenity"="fuel"](around:${Number(radiusMeters)},${Number(lat)},${Number(lon)});
      way["amenity"="fuel"](around:${Number(radiusMeters)},${Number(lat)},${Number(lon)});
      relation["amenity"="fuel"](around:${Number(radiusMeters)},${Number(lat)},${Number(lon)});
    );
    out center tags;
  `;
}

function normalizeStations(elements) {
  const stationsById = new Map();

  (elements || []).forEach((element) => {
    if (element.lat || (element.center && element.center.lat)) {
      stationsById.set(`${element.type}-${element.id}`, element);
    }
  });

  return [...stationsById.values()];
}

async function findFuelStationsByCity(cityName, fallbackCenter) {
  const areaData = await queueOverpassRequest(buildFuelStationQuery(cityName));
  const areaStations = normalizeStations(areaData.elements);

  if (areaStations.length || !fallbackCenter) {
    return areaStations;
  }

  const radiusData = await queueOverpassRequest(
    buildFuelStationRadiusQuery(fallbackCenter.lat, fallbackCenter.lon)
  );

  return normalizeStations(radiusData.elements);
}
