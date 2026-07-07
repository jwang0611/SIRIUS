// ==================== iSDTaiM Particle System ====================
// Advanced Canvas-based particle background with theme awareness
// Supports step-based color schemes and dark/light theme variants

/**
 * Particle class representing a single animated particle
 */
class Particle {
  /**
   * @param {number} canvasWidth - Canvas width
   * @param {number} canvasHeight - Canvas height
   */
  constructor(canvasWidth, canvasHeight) {
    this.reset(canvasWidth, canvasHeight, true);
  }

  /**
   * Reset particle to random position
   * @param {number} canvasWidth - Canvas width
   * @param {number} canvasHeight - Canvas height
   * @param {boolean} randomPosition - Whether to randomize position
   */
  reset(canvasWidth, canvasHeight, randomPosition = false) {
    this.x = randomPosition ? Math.random() * canvasWidth : canvasWidth + Math.random() * 100;
    this.y = Math.random() * canvasHeight;
    this.size = Math.random() * 2.5 + 0.5;
    this.speedX = (Math.random() - 0.5) * 0.4;
    this.speedY = (Math.random() - 0.5) * 0.4;
    this.opacity = Math.random() * 0.5 + 0.2;
    this.pulsePhase = Math.random() * Math.PI * 2;
    this.pulseSpeed = 0.02 + Math.random() * 0.02;
  }

  /**
   * Update particle position and properties
   * @param {number} canvasWidth - Canvas width
   * @param {number} canvasHeight - Canvas height
   */
  update(canvasWidth, canvasHeight) {
    this.x += this.speedX;
    this.y += this.speedY;
    this.pulsePhase += this.pulseSpeed;

    // Wrap around edges with smooth transition
    if (this.x < -10) this.x = canvasWidth + 10;
    if (this.x > canvasWidth + 10) this.x = -10;
    if (this.y < -10) this.y = canvasHeight + 10;
    if (this.y > canvasHeight + 10) this.y = -10;
  }

  /**
   * Get current opacity with pulse effect
   * @returns {number} Current opacity value
   */
  getPulseOpacity() {
    return this.opacity * (0.6 + 0.4 * Math.sin(this.pulsePhase));
  }
}

/**
 * Connection line between particles
 */
class ParticleConnection {
  /**
   * Draw connection line between two particles
   * @param {CanvasRenderingContext2D} ctx - Canvas context
   * @param {Particle} p1 - First particle
   * @param {Particle} p2 - Second particle
   * @param {number} distance - Distance between particles
   * @param {number} maxDistance - Maximum connection distance
   * @param {string} color - Connection color
   * @param {number} baseOpacity - Base opacity for connections
   */
  static draw(ctx, p1, p2, distance, maxDistance, color, baseOpacity) {
    const opacity = (1 - distance / maxDistance) * baseOpacity * 0.3;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.strokeStyle = color;
    ctx.globalAlpha = opacity;
    ctx.lineWidth = 0.5;
    ctx.stroke();
  }
}

/**
 * Main Particle Background System
 * Creates an animated particle field with theme-aware colors
 */
class ParticleBackground {
  /**
   * Color schemes for different steps
   * @type {Object}
   */
  static COLORS = {
    step1: {
      dark: ['#60a5fa', '#8b5cf6', '#3b82f6'],  // Blue/Purple
      light: ['#3b82f6', '#6366f1', '#2563eb']
    },
    step2: {
      dark: ['#a78bfa', '#22d3ee', '#8b5cf6'],  // Purple/Cyan
      light: ['#7c3aed', '#0891b2', '#6366f1']
    },
    step3: {
      dark: ['#34d399', '#22d3ee', '#10b981'],  // Green/Cyan
      light: ['#059669', '#0891b2', '#047857']
    }
  };

  /**
   * @param {string} canvasId - ID of the canvas element
   */
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) {
      console.warn(`Canvas element #${canvasId} not found`);
      return;
    }

    this.ctx = this.canvas.getContext('2d');
    this.particles = [];
    this.currentStep = 1;
    this.isDark = !document.body.classList.contains('light-theme');
    this.animationId = null;
    this.isVisible = true;
    this.mouseX = null;
    this.mouseY = null;
    this.connectionDistance = 120;

    // Performance settings
    this.targetFPS = 30;
    this.frameInterval = 1000 / this.targetFPS;
    this.lastFrameTime = 0;

    this.init();
  }

  /**
   * Initialize the particle system
   */
  init() {
    this.resize();
    this.createParticles();
    this.bindEvents();
    this.animate(0);
  }

  /**
   * Bind window events
   */
  bindEvents() {
    // Resize handler with debounce
    let resizeTimeout;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimeout);
      resizeTimeout = setTimeout(() => this.resize(), 100);
    });

    // Visibility change handler for performance
    document.addEventListener('visibilitychange', () => {
      this.isVisible = !document.hidden;
      if (this.isVisible && !this.animationId) {
        this.animate(0);
      }
    });

    // Mouse interaction
    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      this.mouseX = e.clientX - rect.left;
      this.mouseY = e.clientY - rect.top;
    });

    this.canvas.addEventListener('mouseleave', () => {
      this.mouseX = null;
      this.mouseY = null;
    });

    // Reduced motion preference
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.reducedMotion = motionQuery.matches;
    motionQuery.addEventListener('change', (e) => {
      this.reducedMotion = e.matches;
    });
  }

  /**
   * Resize canvas to match window
   */
  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = window.innerWidth * dpr;
    this.canvas.height = window.innerHeight * dpr;
    this.canvas.style.width = window.innerWidth + 'px';
    this.canvas.style.height = window.innerHeight + 'px';
    this.ctx.scale(dpr, dpr);

    // Adjust particle count based on screen size
    this.adjustParticleCount();
  }

  /**
   * Adjust number of particles based on screen size
   */
  adjustParticleCount() {
    const area = window.innerWidth * window.innerHeight;
    const targetCount = Math.floor(area / 12000); // ~1 particle per 12000px²
    const maxCount = 150;
    const minCount = 30;
    const newCount = Math.min(maxCount, Math.max(minCount, targetCount));

    // Add or remove particles as needed
    while (this.particles.length < newCount) {
      this.particles.push(new Particle(window.innerWidth, window.innerHeight));
    }
    while (this.particles.length > newCount) {
      this.particles.pop();
    }
  }

  /**
   * Create initial particles
   */
  createParticles() {
    const count = Math.floor((window.innerWidth * window.innerHeight) / 12000);
    const particleCount = Math.min(150, Math.max(30, count));

    this.particles = Array.from(
      { length: particleCount },
      () => new Particle(window.innerWidth, window.innerHeight)
    );
  }

  /**
   * Update theme and step colors
   * @param {number} step - Current step (1, 2, or 3)
   * @param {boolean} isDark - Whether dark theme is active
   */
  updateTheme(step, isDark) {
    this.currentStep = step;
    this.isDark = isDark;
  }

  /**
   * Get current color scheme
   * @returns {string[]} Array of colors for current theme/step
   */
  getCurrentColors() {
    const stepKey = `step${this.currentStep}`;
    const themeKey = this.isDark ? 'dark' : 'light';
    return ParticleBackground.COLORS[stepKey]?.[themeKey] || ParticleBackground.COLORS.step1.dark;
  }

  /**
   * Draw a single particle
   * @param {Particle} particle - The particle to draw
   * @param {string} color - The color to use
   * @param {number} baseOpacity - Base opacity multiplier
   */
  drawParticle(particle, color, baseOpacity) {
    const opacity = particle.getPulseOpacity() * baseOpacity;

    // Glow effect
    const gradient = this.ctx.createRadialGradient(
      particle.x, particle.y, 0,
      particle.x, particle.y, particle.size * 3
    );
    gradient.addColorStop(0, color);
    gradient.addColorStop(1, 'transparent');

    this.ctx.beginPath();
    this.ctx.arc(particle.x, particle.y, particle.size * 2, 0, Math.PI * 2);
    this.ctx.fillStyle = gradient;
    this.ctx.globalAlpha = opacity * 0.3;
    this.ctx.fill();

    // Core particle
    this.ctx.beginPath();
    this.ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
    this.ctx.fillStyle = color;
    this.ctx.globalAlpha = opacity;
    this.ctx.fill();
  }

  /**
   * Draw connections between nearby particles
   * @param {number} baseOpacity - Base opacity for connections
   */
  drawConnections(baseOpacity) {
    const colors = this.getCurrentColors();
    const connectionColor = colors[0];

    for (let i = 0; i < this.particles.length; i++) {
      for (let j = i + 1; j < this.particles.length; j++) {
        const dx = this.particles[i].x - this.particles[j].x;
        const dy = this.particles[i].y - this.particles[j].y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance < this.connectionDistance) {
          ParticleConnection.draw(
            this.ctx,
            this.particles[i],
            this.particles[j],
            distance,
            this.connectionDistance,
            connectionColor,
            baseOpacity
          );
        }
      }
    }
  }

  /**
   * Apply mouse interaction effects
   */
  applyMouseInteraction() {
    if (this.mouseX === null || this.mouseY === null) return;

    const interactionRadius = 150;
    for (const particle of this.particles) {
      const dx = particle.x - this.mouseX;
      const dy = particle.y - this.mouseY;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (distance < interactionRadius) {
        const force = (1 - distance / interactionRadius) * 0.5;
        particle.x += dx * force * 0.02;
        particle.y += dy * force * 0.02;
      }
    }
  }

  /**
   * Main animation loop
   * @param {number} currentTime - Current timestamp
   */
  animate(currentTime) {
    if (!this.isVisible || this.reducedMotion) {
      this.animationId = requestAnimationFrame((t) => this.animate(t));
      return;
    }

    // Frame rate limiting
    const deltaTime = currentTime - this.lastFrameTime;
    if (deltaTime < this.frameInterval) {
      this.animationId = requestAnimationFrame((t) => this.animate(t));
      return;
    }
    this.lastFrameTime = currentTime - (deltaTime % this.frameInterval);

    const colors = this.getCurrentColors();
    const baseOpacity = this.isDark ? 0.7 : 0.4;

    // Clear canvas
    this.ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

    // Draw connections first (behind particles)
    this.drawConnections(baseOpacity);

    // Update and draw particles
    for (let i = 0; i < this.particles.length; i++) {
      const particle = this.particles[i];
      particle.update(window.innerWidth, window.innerHeight);

      const color = colors[i % colors.length];
      this.drawParticle(particle, color, baseOpacity);
    }

    // Apply mouse interaction
    this.applyMouseInteraction();

    // Reset global alpha
    this.ctx.globalAlpha = 1;

    this.animationId = requestAnimationFrame((t) => this.animate(t));
  }

  /**
   * Destroy the particle system
   */
  destroy() {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
    }
    this.particles = [];
  }
}

// Export for use in app.js
window.ParticleBackground = ParticleBackground;

