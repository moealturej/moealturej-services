document.addEventListener('DOMContentLoaded', () => {
  // Stripe initialization
  const stripe = Stripe('YOUR_PUBLISHABLE_KEY');  // Replace with actual key if needed
  const slug = document.body.dataset.slug;
  
  // Plan selection handling
  const priceButtons = document.querySelectorAll('.plan-btn');
  const selectedPriceDisplay = document.getElementById('selected-price');
  let selectedPlan = 'day';
  let selectedPrice = priceButtons[0].dataset.price;  // Default price to first button price

  priceButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      priceButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedPlan = btn.dataset.plan;
      selectedPrice = btn.dataset.price;
      selectedPriceDisplay.textContent = `Selected Plan: ${btn.textContent}`;
    });
  });

document.getElementById('checkout-button').addEventListener('click', () => {
  const email = document.getElementById("email").value.trim();

  // Validation
  if (!email || !email.includes('@')) {
    alert("⚠️ Please enter a valid email address.");
    return;
  }

  if (!selectedPlan || !selectedPrice) {
    alert("⚠️ Please select a plan.");
    return;
  }

  // Log selected parameters for debugging
  console.log(`Selected Plan: ${selectedPlan}, Selected Price: ${selectedPrice}, Email: ${email}`);

  // Send a POST request to create checkout session with query parameters for plan and price
  fetch(`/create-checkout-session/${slug}?plan=${encodeURIComponent(selectedPlan)}&price=${encodeURIComponent(selectedPrice)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email: email }) // Send email as part of the request body
  })
  .then(res => res.json())
  .then(session => {
    if (!session.id) {
      throw new Error("Invalid response from server: No session ID");
    }
    // Redirect to Stripe Checkout
    stripe.redirectToCheckout({ sessionId: session.id })
      .then(result => {
        if (result.error) {
          throw new Error(result.error.message);
        }
      });
  })
  .catch(err => {
    console.error("❌ Payment Error: ", err.message);
    alert(`❌ Payment Error: ${err.message}`);
  });
});

  // Particles.js initialization
  particlesJS.load('particles-js', '/static/particles.json', () => {
    console.log('Particles loaded');
  });
});
