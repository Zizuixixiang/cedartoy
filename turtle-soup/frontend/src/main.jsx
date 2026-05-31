import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import App from './App.jsx'
import Lobby from './pages/Lobby.jsx'
import Room from './pages/Room.jsx'
import Profile from './pages/Profile.jsx'
import Admin from './pages/Admin.jsx'
import AddPuzzle from './pages/AddPuzzle.jsx'
import './styles/global.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename="/soup">
      <Routes>
        <Route element={<App />}>
          <Route index element={<Lobby />} />
          <Route path="login" element={<Navigate to="/" replace />} />
          <Route path="room/:roomId" element={<Room />} />
          <Route path="profile" element={<Profile />} />
          <Route path="admin" element={<Admin />} />
          <Route path="add-puzzle" element={<AddPuzzle />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
