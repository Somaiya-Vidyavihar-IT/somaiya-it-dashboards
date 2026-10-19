// Marks a dashboard shown inside the hub so its CSS can drop the logo, account, and disconnect controls the hub already shows.
(function(){
  try{if(window.parent!==window&&window.parent.location.origin===window.location.origin)document.documentElement.classList.add('embedded');}catch(e){}
})();
