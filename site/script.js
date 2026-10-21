// eitri.io: copy the install line, honour reduced motion for the hero video, and post the waitlist form.
(function () {
  'use strict';

  // Install: click to copy the one-line install. The command is the text of the <pre>, nothing hidden.
  var install = document.getElementById('install');
  var cmd = document.getElementById('installcmd');
  function copyCommand() {
    var text = cmd.textContent.trim();
    var done = function () {
      install.textContent = 'Copied';
      install.classList.add('copied');
      setTimeout(function () { install.textContent = 'Install'; install.classList.remove('copied'); }, 1800);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { selectCommand(); });
    } else {
      selectCommand();
    }
  }
  function selectCommand() {            // no clipboard access: select the line so Ctrl/Cmd-C works
    var range = document.createRange();
    range.selectNodeContents(cmd);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    cmd.focus();
  }
  if (install && cmd) {
    install.addEventListener('click', copyCommand);
    cmd.addEventListener('click', copyCommand);
  }

  // The hero video is the page's only autonomous motion. With reduced motion it waits for a play press.
  var hero = document.getElementById('hero');
  if (hero) {
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    var apply = function () {
      if (reduce.matches) { hero.removeAttribute('autoplay'); hero.pause(); hero.controls = true; }
    };
    apply();
    if (reduce.addEventListener) reduce.addEventListener('change', apply);
    if ('IntersectionObserver' in window && !reduce.matches) {   // off screen: pause, so it never plays unseen
      new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { hero.play().catch(function () {}); } else { hero.pause(); } });
      }, { threshold: 0.2 }).observe(hero);
    }
  }

  // Waitlist: POST to Formspree with fetch; the page never navigates away.
  var form = document.getElementById('waitlist');
  if (form) {
    var msg = form.querySelector('.form-msg');
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      msg.className = 'form-msg';
      if (form.action.indexOf('REPLACE_ME') !== -1) {
        msg.textContent = 'The waitlist is not connected yet. Come back soon.';
        msg.classList.add('bad');
        return;
      }
      var button = form.querySelector('button');
      button.disabled = true;
      msg.textContent = 'Sending';
      fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } })
        .then(function (r) {
          if (!r.ok) throw new Error('bad status ' + r.status);
          form.classList.add('done');
          msg.textContent = 'Thanks. You will hear from us when the hosted version opens.';
          msg.classList.add('ok');
        })
        .catch(function () {
          msg.textContent = 'That did not go through. Please try again in a moment.';
          msg.classList.add('bad');
        })
        .then(function () { button.disabled = false; });
    });
  }
})();
