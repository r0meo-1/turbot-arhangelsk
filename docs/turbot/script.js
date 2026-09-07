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
function showQuestion(){
  if(step >= questions.length){showResult(); return;}
  const q = questions[step];
  bar.style.width = ((step + 1) / (questions.length + 1) * 100) + '%';
  quiz.innerHTML = '<div class="quiz-q"><h3>'+q[0]+'</h3><div class="options">'+q[1].map(function(x){return '<button class="option" data-value="'+x+'">'+x+'</button>';}).join('')+'</div></div>';
  quiz.querySelectorAll('.option').forEach(function(btn){
    btn.addEventListener('click', function(){answers[step] = btn.dataset.value; step += 1; showQuestion();});
  });
}
function showResult(){
  bar.style.width = '100%';
  quiz.innerHTML = '<div class="quiz-q"><span class="eyebrow">✓ Заявка готова</span><h3 style="margin-top:14px">Ваш запрос</h3><div class="summary"><div class="summary-row"><span>Направление</span><b>'+answers[0]+'</b></div><div class="summary-row"><span>Когда</span><b>'+answers[1]+'</b></div><div class="summary-row"><span>Бюджет</span><b>'+answers[2]+'</b></div><div class="summary-row"><span>Туристы</span><b>'+answers[3]+'</b></div></div><a class="btn btn-vk" href="https://vk.ru/club240310110" target="_blank" rel="noopener">Отправить менеджеру в VK</a></div>';
}
showQuestion();