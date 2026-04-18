const state = {
  products: [],
  filtered: [],
  cart: new Map(),
};

const productGrid = document.getElementById('productGrid');
const cartItems = document.getElementById('cartItems');
const cartTotal = document.getElementById('cartTotal');
const searchInput = document.getElementById('searchInput');
const categoryFilter = document.getElementById('categoryFilter');
const checkoutForm = document.getElementById('checkoutForm');
const imageModal = document.getElementById('imageModal');
const imageModalPreview = document.getElementById('imageModalPreview');
const imageModalTitle = document.getElementById('imageModalTitle');
const imageModalText = document.getElementById('imageModalText');
const closeImageModal = document.getElementById('closeImageModal');

const money = (value) => `$${new Intl.NumberFormat('es-CO').format(Number(value || 0))}`;

async function loadCatalog() {
  const response = await fetch('/api/products');
  const data = await response.json();
  state.products = data.products;
  state.filtered = data.products;
  fillCategories();
  renderProducts();
  renderCart();
}

function fillCategories() {
  const categories = [...new Set(state.products.map((item) => item.category).filter(Boolean))].sort();
  categoryFilter.innerHTML = '<option value="">Todas las categorias</option>' +
    categories.map((category) => `<option value="${category}">${category}</option>`).join('');
}

function filterProducts() {
  const term = searchInput.value.trim().toLowerCase();
  const category = categoryFilter.value;
  state.filtered = state.products.filter((item) => {
    const matchesTerm = !term || item.name.toLowerCase().includes(term) || (item.reference || '').toLowerCase().includes(term);
    const matchesCategory = !category || item.category === category;
    return matchesTerm && matchesCategory;
  });
  renderProducts();
}

function renderProducts() {
  productGrid.innerHTML = state.filtered.map((item) => `
    <article class="product-card">
      <button type="button" class="product-image-button" onclick="openImageModal(${item.id})" aria-label="Ver imagen ampliada de ${item.name}">
        <img src="${item.image_path}" alt="${item.name}">
        <span class="image-zoom-label">Toca para ampliar</span>
      </button>
      <div class="product-body">
        <span class="pill">${item.category}</span>
        <h3>${item.name}</h3>
        <p>${item.promotion_text || item.description || 'Producto seleccionado del catalogo.'}</p>
        <small>Ref. ${item.reference || 'Catalogo'} | Stock: ${item.stock}</small>
        <div class="price-row">
          <strong>${money(item.sale_price)}</strong>
        </div>
        <div class="cart-controls">
          <input type="number" min="1" max="${item.stock}" value="1" id="qty-${item.id}" ${item.stock <= 0 ? 'disabled' : ''}>
          <button class="button primary" onclick="addToCart(${item.id})" ${item.stock <= 0 ? 'disabled' : ''}>
            ${item.stock > 0 ? 'Agregar' : 'Agotado'}
          </button>
        </div>
      </div>
    </article>
  `).join('');
}

window.openImageModal = function openImageModal(productId) {
  const product = state.products.find((item) => item.id === productId);
  if (!product || !imageModal) return;

  imageModalPreview.src = product.image_path;
  imageModalPreview.alt = product.name;
  imageModalTitle.textContent = product.name;
  imageModalText.textContent = product.promotion_text || product.description || 'Vista ampliada del producto seleccionado.';
  imageModal.hidden = false;
  document.body.classList.add('modal-open');
};

function closeModal() {
  if (!imageModal) return;
  imageModal.hidden = true;
  imageModalPreview.src = '';
  imageModalPreview.alt = '';
  imageModalTitle.textContent = '';
  imageModalText.textContent = '';
  document.body.classList.remove('modal-open');
}

window.addToCart = function addToCart(productId) {
  const product = state.products.find((item) => item.id === productId);
  const input = document.getElementById(`qty-${productId}`);
  const quantity = Number(input.value || 1);
  if (!product || quantity < 1) return;
  const current = state.cart.get(productId) || 0;
  const next = Math.min(product.stock, current + quantity);
  state.cart.set(productId, next);
  renderCart();
};

function renderCart() {
  const entries = [...state.cart.entries()].map(([productId, quantity]) => {
    const product = state.products.find((item) => item.id === productId);
    return { product, quantity };
  }).filter((entry) => entry.product);

  if (!entries.length) {
    cartItems.innerHTML = '<p class="empty-state">Todavia no has agregado productos.</p>';
    cartTotal.textContent = money(0);
    return;
  }

  cartItems.innerHTML = entries.map(({ product, quantity }) => `
    <article class="cart-item">
      <div>
        <strong>${product.name}</strong>
        <small>${quantity} x ${money(product.sale_price)}</small>
      </div>
      <button type="button" class="remove-link" onclick="removeFromCart(${product.id})">Quitar</button>
    </article>
  `).join('');

  const total = entries.reduce((sum, entry) => sum + (entry.product.sale_price * entry.quantity), 0);
  cartTotal.textContent = money(total);
}

window.removeFromCart = function removeFromCart(productId) {
  state.cart.delete(productId);
  renderCart();
};

checkoutForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.cart.size) {
    alert('Agrega al menos un producto.');
    return;
  }

  const payload = Object.fromEntries(new FormData(checkoutForm).entries());
  payload.items = [...state.cart.entries()].map(([product_id, quantity]) => ({ product_id, quantity }));

  const response = await fetch('/api/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) {
    alert(data.message || 'No se pudo guardar el pedido.');
    return;
  }

  alert(`Pedido #${data.order_id} guardado correctamente. Ahora te llevaremos a WhatsApp para enviarlo.`);
  state.cart.clear();
  checkoutForm.reset();
  if (data.whatsapp_url) {
    window.location.href = data.whatsapp_url;
    return;
  }
  await loadCatalog();
});

searchInput.addEventListener('input', filterProducts);
categoryFilter.addEventListener('change', filterProducts);

if (closeImageModal) {
  closeImageModal.addEventListener('click', closeModal);
}

if (imageModal) {
  imageModal.addEventListener('click', (event) => {
    if (event.target === imageModal) {
      closeModal();
    }
  });
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeModal();
  }
});

loadCatalog();
