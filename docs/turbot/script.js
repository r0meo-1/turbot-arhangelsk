const quiz = document.getElementById('quizBody');
const bar = document.getElementById('progressBar');

const questions = [
  ['Куда хотите поехать? 🌍', ['Турция','Таиланд','Вьетнам','ОАЭ','Египет','Пока не знаю']],
  ['Когда планируете отдых? 📅', ['Июнь','Июль','Август','Сентябрь','Зима','Даты гибкие']],
  ['Какой бюджет на поездку? 💰', ['До 100 000 ₽','До 150 000 ₽','До 200 000 ₽','До 250 000 ₽','До 300 000 ₽','Обсудим']],
  ['Сколько путешественников? 👥', ['1 человек','2 взрослых','2 взрослых + ребёнок','Семья 4+','Компания']]
];

const answers = [];
let step = 0;

function sourceLabel() {
  const p = new URLSearchParams(location.search);
  return p.get('utm_source') || p.get('source') || document.referrer || 'прямой переход';
}

function leadText() {
  return [
    'Новая заявка TurBot',
    'Направление: ' + answers[0],
    'Когда: ' + answers[1],
    'Бюджет: ' + answers[2],
    'Туристы: ' + answers[3],
    'Источник: ' + sourceLabel()
  ].join('\n');
}

function showQuestion() {
  if (step >= questions.length) {
    showResult();
    return;
  }

  const q = questions[step];
  bar.style.width = ((step + 1) / (questions.length + 1) * 100) + '%';
  quiz.innerHTML = '<div class="quiz-q"><h3>' + q[0] + '</h3><div class="options">' +
    q[1].map(function (x) {
      return '<button class="option" data-value="' + x + '">' + x + '</button>';
    }).join('') + '</div></div>';

  quiz.querySelectorAll('.option').forEach(function (btn) {
    btn.addEventListener('click', function () {
      answers[step] = btn.dataset.value;
      step += 1;
      showQuestion();
    });
  });
}

function showResult() {
  bar.style.width = '100%';
  const text = leadText();
  const tgUrl = 'https://t.me/share/url?url=' + encodeURIComponent(location.href.split('?')[0]) + '&text=' + encodeURIComponent(text);
  const vkUrl = 'https://vk.me/club240310110';

  quiz.innerHTML = [
    '<div class="quiz-q">',
    '<span class="eyebrow">✓ Заявка готова</span>',
    '<h3 style="margin-top:14px">Ваш запрос</h3>',
    '<div class="summary">',
    '<div class="summary-row"><span>Направление</span><b>' + answers[0] + '</b></div>',
    '<div class="summary-row"><span>Когда</span><b>' + answers[1] + '</b></div>',
    '<div class="summary-row"><span>Бюджет</span><b>' + answers[2] + '</b></div>',
    '<div class="summary-row"><span>Туристы</span><b>' + answers[3] + '</b></div>',
    '</div>',
    '<div class="copybox" id="leadText"></div>',
    '<div class="quiz-controls">',
    '<button class="btn btn-ghost" id="restartQuiz">Заполнить заново</button>',
    '<button class="btn btn-primary" id="copyLead">Скопировать заявку</button>',
    '</div>',
    '<div class="cta-actions" style="margin-top:14px">',
    '<a class="btn btn-vk" href="' + vkUrl + '" target="_blank" rel="noopener">Написать менеджеру в VK</a>',
    '<a class="btn btn-ghost" href="' + tgUrl + '" target="_blank" rel="noopener">Отправить через Telegram</a>',
    '</div>',
    '<p style="margin:14px 0 0;color:#718397;font-size:12px">Telegram откроет окно отправки готового текста. Прямая ссылка на Telegram-бота будет подключена отдельно, когда появится его публичный username.</p>',
    '</div>'
  ].join('');

  document.getElementById('leadText').textContent = text;

  document.getElementById('copyLead').addEventListener('click', async function () {
    try {
      await navigator.clipboard.writeText(text);
      this.textContent = 'Скопировано ✓';
    } catch (e) {
      const box = document.getElementById('leadText');
      const range = document.createRange();
      range.selectNodeContents(box);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  });

  document.getElementById('restartQuiz').addEventListener('click', function () {
    answers.length = 0;
    step = 0;
    showQuestion();
  });
}

showQuestion();