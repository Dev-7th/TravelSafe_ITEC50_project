const statusElement = document.getElementById("map-status");
const cityInput = document.getElementById("city-input");
let latestSearchId = 0;

function setMapStatus(message, type = "info") {
  if (!statusElement) {
    return;
  }

  statusElement.textContent = message;
  statusElement.dataset.type = type;
}

function getAddressCity(address) {
  return address.city
    || address.town
    || address.municipality
    || address.village
    || address.county
    || address.state_district;
}

async function fetchCityPrices(city) {
  const response = await fetch(`/api/city-fuel-prices?city=${encodeURIComponent(city)}`);
  const data = await response.json();

  if (!response.ok || !data.found) {
    throw new Error(data.message || `No fuel price data found for ${city}.`);
  }

  return data;
}

async function fetchNearestPrices(city, coordinates) {
  if (!coordinates) {
    return fetchCityPrices(city);
  }

  const params = new URLSearchParams({
    city,
    lat: coordinates.lat,
    lng: coordinates.lon,
  });
  const response = await fetch(`/api/get-nearest-prices?${params.toString()}`);
  const data = await response.json();

  if (!response.ok || !data.found) {
    throw new Error(data.message || `No fuel price data found near ${city}.`);
  }

  return data;
}

async function fetchCityCenter(city, province) {
  const query = [city, province, "Philippines"].filter(Boolean).join(", ");
  const response = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1&addressdetails=1`);

  if (!response.ok) {
    return null;
  }

  const data = await response.json();
  if (!data.length) {
    return null;
  }

  return {
    lat: Number(data[0].lat),
    lon: Number(data[0].lon),
  };
}

async function executeFuelSearch(city, fallbackCenter = null) {
  const searchId = ++latestSearchId;
  const cleanCity = String(city || "").trim();
  if (!cleanCity) {
    setMapStatus("Enter a city or use Near Me to start the live search.", "error");
    return;
  }

  try {
    setMapStatus(`Finding coordinates for ${cleanCity}...`);
    const cityCenter = fallbackCenter || await fetchCityCenter(cleanCity);

    if (searchId !== latestSearchId) {
      return;
    }

    setMapStatus(`Fetching latest fuel prices for ${cleanCity}...`);
    const cityPriceData = await fetchNearestPrices(cleanCity, cityCenter);

    if (searchId !== latestSearchId) {
      return;
    }

    setMapStatus(`Searching physical fuel stations in ${cleanCity}...`);
    const stations = await findFuelStationsByCity(cleanCity, cityCenter);

    if (searchId !== latestSearchId) {
      return;
    }

    if (!stations.length) {
      stationLayer.clearLayers();
      setMapStatus(`No fuel stations found in OpenStreetMap for ${cleanCity}.`, "error");
      return;
    }

    const summary = renderFuelStations(stations, cityPriceData);
    const fallbackMessage = cityPriceData.fallback_used
      ? `Prices for ${cleanCity} are unavailable. Showing prices from nearest city: ${cityPriceData.source_city}. `
      : "";
    setMapStatus(
      `${fallbackMessage}Found ${summary.totalStations} stations. ${summary.matchedStations} assigned DB prices, including ${summary.independentStations} independent stations. Green pins show the lowest available price.`,
      "success"
    );
  } catch (error) {
    console.error(error);
    setMapStatus(error.message || "Fuel station search failed.", "error");
  }
}

function searchCity() {
  executeFuelSearch(cityInput.value);
}

function getLocation() {
  if (!navigator.geolocation) {
    setMapStatus("Geolocation is not supported by this browser.", "error");
    return;
  }

  setMapStatus("Finding your location...");
  navigator.geolocation.getCurrentPosition(async (position) => {
    const lat = position.coords.latitude;
    const lon = position.coords.longitude;

    try {
      map.setView([lat, lon], 14);
      setMapStatus("Finding your city from your location...");

      const response = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&addressdetails=1`);
      if (!response.ok) {
        throw new Error("Nominatim could not reverse geocode your location.");
      }

      const data = await response.json();
      const city = getAddressCity(data.address || {});

      if (!city) {
        throw new Error("Could not detect a city from your current location.");
      }

      cityInput.value = city;
      executeFuelSearch(city, { lat, lon });
    } catch (error) {
      console.error(error);
      setMapStatus(error.message || "Near Me search failed.", "error");
    }
  }, () => {
    setMapStatus("Location permission was denied or unavailable.", "error");
  });
}

if (cityInput) {
  cityInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      searchCity();
    }
  });
}
